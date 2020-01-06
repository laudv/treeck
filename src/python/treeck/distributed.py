import codecs, time, io, json, timeit

from enum import Enum
from dask.distributed import wait, get_worker

from .pytreeck import Subspace
from .verifier import Verifier, VerifierTimeout, VerifierNotExpr


class DistributedVerifier:

    def __init__(self,
            client,
            sb,
            verifier_factory,
            check_paths = True,
            saturate_workers_from_start = False,
            saturate_workers_factor = 1.0,
            num_initial_tasks = 1,
            stop_when_sat = False,
            timeout_start = 5,
            timeout_max = 120,
            timeout_grow_rate = 1.2):

        self._timeout_start = timeout_start
        self._timeout_max = timeout_max
        self._timeout_rate = timeout_grow_rate

        self._client = client # dask client
        self._sb = sb
        self._at = sb.addtree()
        self._at_fut = client.scatter(self._at, broadcast=True) # distribute addtree to all workers
        self._verifier_factory = verifier_factory # (addtree, splittree_leaf) -> Verifier

        self._check_paths_opt = check_paths
        self._saturate_workers_opt = saturate_workers_from_start
        self._saturate_workers_factor_opt = saturate_workers_factor
        self._num_initial_tasks_opt = num_initial_tasks
        self._stop_when_sat_opt = stop_when_sat

        self._stop_flag = False

    def check(self):
        self.done_count = 0
        self.start_time = timeit.default_timer()
        self.results = {0: {}} # domtree_node_id => result info
        l0 = self._sb.get_subspace(0)

        # TODO add domain constraints to verifier

        # 1: loop over trees, check reachability of each path from root
        if self._check_paths_opt:
            l0 = self._check_paths(l0)

        # 2: splits until we have a piece of work for each worker
        if self._saturate_workers_opt:
            nworkers = sum(self._client.nthreads().values())
            ntasks = int(round(self._saturate_workers_factor_opt * nworkers))
            ls = self._generate_splits(l0, ntasks)
        elif self._num_initial_tasks_opt > 1:
            ls = self._generate_splits(l0, self._num_initial_tasks_opt)
        else:
            ls = [l0]

        # 3: submit verifier 'check' tasks for each
        self._fs = [self._client.submit(DistributedVerifier._verify_fun,
            self._at_fut, lk, self._timeout_start, self._verifier_factory)
            for lk in ls]

        # 4: wait for future to complete, act on result
        # - if sat/unsat -> done (finish if sat if opt set)
        # - if new split -> schedule two new tasks
        while len(self._fs) > 0: # while task are running...
            if self._stop_flag:
                self._print("Stop flag: cancelling remaining tasks")
                for f in self._fs:
                    f.cancel()
                    self._stop_flag = False
                break

            wait(self._fs, return_when="FIRST_COMPLETED")
            next_fs = []
            for f in self._fs:
                if f.done(): next_fs += self._handle_done_future(f)
                else:        next_fs.append(f)
            self._fs = next_fs

    def _check_paths(self, l0):
        # update reachabilities in splittree_leaf 0 in parallel
        t0 = timeit.default_timer()
        fs = []
        for tree_index in range(len(self._at)):
            f = self._client.submit(DistributedVerifier._check_tree_paths,
                self._at_fut,
                tree_index,
                l0,
                self._verifier_factory)
            fs.append(f)
            pass
        wait(fs)
        for f in fs:
            assert f.done()
            assert f.exception() is None
        t1 = timeit.default_timer()
        if hasattr(self, "results"):
            self.results[l0.domtree_node_id()]["check_paths_time"] = t1 - t0
        return Subspace.merge(list(map(lambda f: f.result(), fs)))

    def _generate_splits(self, l0, ntasks):
        # split and collect splittree_leafs; this runs locally
        l0.find_best_domtree_split(self._at)
        ls = [l0]
        while len(ls) < ntasks:
            max_score = 0
            max_lk = None
            for lk in ls:
                if lk.split_score > max_score:
                    max_score = lk.split_score
                    max_lk = lk

            if max_lk is None:
                raise RuntimeError("no more splits!")

            ls.remove(max_lk)
            nid = max_lk.domtree_node_id()

            #print("splitting domtree_node_id", nid, max_lk.get_best_split())
            self._sb.split(max_lk)

            domtree = self._sb.domtree()
            l, r = domtree.left(nid), domtree.right(nid)
            self.results[l] = {}
            self.results[r] = {}
            ll, lr = self._sb.get_subspace(l), self._sb.get_subspace(r)
            ll.find_best_domtree_split(self._at)
            lr.find_best_domtree_split(self._at)
            ls += [ll, lr]

        return ls

    def _handle_done_future(self, f):
        t = f.result()
        status = t[0]

        # We're finished with this branch!
        if status != Verifier.Result.UNKNOWN:
            self.done_count += 1
            model, domtree_node_id, check_time = t[1:]
            self._print(f"{status} for {domtree_node_id} in {check_time:.2f}s")
            r = self.results[domtree_node_id]
            r["status"] = status
            r["check_time"] = check_time
            r["model"] = model
            if status.is_sat() and self._stop_when_sat_opt:
                self._stop_flag = True
            return []

        # We timed out, split and try again
        lk, check_time, timeout = t[1:]
        domtree_node_id = lk.domtree_node_id()
        split = lk.get_best_split()
        score, balance = lk.split_score, lk.split_balance

        self._sb.split(lk)

        domtree = self._sb.domtree()
        domtree_node_id_l = domtree.left(domtree_node_id)
        domtree_node_id_r = domtree.right(domtree_node_id)
        self.results[domtree_node_id]["check_time"] = check_time
        self.results[domtree_node_id_l] = {}
        self.results[domtree_node_id_r] = {}

        self._print(f"TIMEOUT for {domtree_node_id} in {check_time:.1f}s (timeout={timeout:.1f})")
        self._print(f"> splitting {domtree_node_id} into {domtree_node_id_l}, {domtree_node_id_r} with score {score}")

        next_timeout = min(self._timeout_max, self._timeout_rate * timeout)

        fs = [self._client.submit(DistributedVerifier._verify_fun,
            self._at_fut, lk, next_timeout, self._verifier_factory)
            for lk in [self._sb.get_subspace(domtree_node_id_l),
                       self._sb.get_subspace(domtree_node_id_r)]]
        return fs

    def _print(self, msg):
        time = int(timeit.default_timer() - self.start_time)
        m, s = time // 60, time % 60
        done = self.done_count
        rem = len(self._fs)
        print(f"[{m}m{s:02d}s {done:>4} {rem:<4}]", msg)





    # - WORKERS ------------------------------------------------------------- #

    @staticmethod
    def _check_tree_paths(at, tree_index, l0, vfactory):
        tree = at[tree_index]
        stack = [(tree.root(), True)]
        v = vfactory(at, l0)

        while len(stack) > 0:
            node, path_constraints = stack.pop()

            l, r = tree.left(node), tree.right(node)
            split = tree.get_split(node) # (split_type, feat_id...)
            xvar = v.xvar(split[1])
            if split[0] == "lt":
                split_value = split[2]
                constraint_l = (xvar < split_value)
                constraint_r = (xvar >= split_value)
            elif split[0] == "bool":
                constraint_l = VerifierNotExpr(xvar) # false left, true right
                constraint_r = xvar
            else: raise RuntimeError(f"unknown split type {split[0]}")

            if tree.is_internal(l) and l0.is_reachable(tree_index, l):
                path_constraints_l = constraint_l & path_constraints;
                if v.check(path_constraints_l).is_sat():
                    stack.append((l, path_constraints_l))
                else:
                    print(f"unreachable  left: {tree_index} {l}")
                    l0.mark_unreachable(tree_index, l)

            if tree.is_internal(r) and l0.is_reachable(tree_index, r):
                path_constraints_r = constraint_r & path_constraints;
                if v.check(path_constraints_r).is_sat():
                    stack.append((r, path_constraints_r))
                else:
                    print(f"unreachable right: {tree_index} {r}")
                    l0.mark_unreachable(tree_index, r)

        return l0

    @staticmethod
    def _verify_fun(at, lk, timeout, vfactory):
        v = vfactory(at, lk)
        v.set_timeout(timeout)
        v.add_all_trees()
        try:
            status = v.check()
            model = {}
            if status.is_sat():
                model = v.model()
                model["family"] = v.model_family(model)
            return status, model, lk.domtree_node_id(), v.check_time
        except VerifierTimeout as e:
            print(f"timeout after {e.unk_after} (timeout = {timeout}) -> splitting l{lk.domtree_node_id()}")
            lk.find_best_domtree_split(at)
            return Verifier.Result.UNKNOWN, lk, v.check_time, timeout
