import timeit, math

from dask.distributed import wait

from .pytreeck import Subspace
from .verifier import Verifier, VerifierTimeout, VerifierNotExpr


class VerifierFactory:
    """ Must be pickleable """

    def __call__(self, addtree_of_each_instance, subspace_of_each_instance):
        """ Override this method for your verifier factory.  """
        raise RuntimeError("Override this method in your own verifier "
            + "factory defining your problem's constraints.")

    def inv_logit(self, prob):
        return -math.log(1.0 / x - 1)



class DistributedVerifier:

    class Instance:
        def __init__(self, index, client, subspaces):
            self.index = index
            self.addtree = subspaces.addtree()
            self.subspaces = subspaces

    def __init__(self,
            client,
            subspaces,
            verifier_factory,
            check_paths = True,
            num_initial_tasks = 1,
            stop_when_sat = False,
            timeout_start = 30,
            timeout_max = 600,
            timeout_grow_rate = 1.5):

        assert isinstance(verifier_factory, VerifierFactory), "invalid verifier factory"

        self._timeout_start = float(timeout_start)
        self._timeout_max = float(timeout_max)
        self._timeout_rate = float(timeout_grow_rate)

        self._client = client # dask client
        if not isinstance(subspaces, list):
            subspaces = [subspaces]

        self._instances = [DistributedVerifier.Instance(index,
            self._client, sb) for index, sb in enumerate(subspaces)]
        self._addtrees = [inst.addtree for inst in self._instances]
        self._addtrees_fut = client.scatter(self._addtrees, broadcast=True)

        # ([addtree], [subspace]) -> Verifier
        self._verifier_factory = verifier_factory

        self._check_paths_opt = check_paths
        self._num_initial_tasks_opt = num_initial_tasks
        self._stop_when_sat_opt = stop_when_sat

        self._stop_flag = False
        self._print_queue = []

    def check(self):
        self.done_count = 0
        self.start_time = timeit.default_timer()

        self._fs = []
        self._split_id = 0

        # TODO add domain constraints to verifier

        # 1: loop over trees, check reachability of each path from root in
        # addtrees of all instances
        ls = [inst.subspaces.get_subspace(0) for inst in self._instances]
        if self._check_paths_opt:
            t0 = timeit.default_timer()
            ls = [inst.subspaces.get_subspace(0) for inst in self._instances]
            ls = self._check_paths(ls)
            t1 = timeit.default_timer()
            self.results["check_paths_time"] = t1 - t0
            self.results[0]["num_unreachable"] = self._num_unreachable(ls)

        # split_id => result info per instance + additional info
        self.results = {}
        self.results["num_leafs"] = [inst.addtree.num_leafs() for inst in self._instances]
        self.results["num_nodes"] = [inst.addtree.num_nodes() for inst in self._instances]
        self.results[0] = self._init_results(ls)

        # 2: splits until we have a piece of work for each worker
        if self._num_initial_tasks_opt > 1:
            t0 = timeit.default_timer()
            lss = self._generate_splits(ls, self._num_initial_tasks_opt)
            t1 = timeit.default_timer()
            self.results["generate_splits_time"] = t1 - t0
        else:
            lss = [(self._new_split_id(), ls)]


        # 3: submit verifier 'check' tasks for each item in `ls`
        for split_id, ls in lss:
            f = self._make_verify_future(split_id, ls, self._timeout_start)
            self._fs.append(f)

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
            self._print_flush()

    def _check_paths(self, ls):
        # update reachabilities in domtree root leaf 0 in parallel
        fs = []
        for instance in self._instances:
            for tree_index in range(len(instance.addtree)):
                f = self._client.submit(DistributedVerifier._check_tree_paths,
                        self._addtrees_fut, ls, instance.index, tree_index,
                        self._verifier_factory)
                fs.append(f)
        wait(fs)
        for f in fs:
            if not f.done():
                raise RuntimeError("future not done?")
            if f.exception():
                raise f.exception() from RuntimeError("exception on worker")
        return Subspace.merge(list(map(lambda f: f.result(), fs)))

    def _generate_splits(self, ls, ntasks):
        # split domtrees until we have ntask `Subspace`s; this runs locally
        lss = [(self._new_split_id(), ls)]
        for instance_index, lk in enumerate(ls):
            lk.find_best_domtree_split(self._instances[instance_index].addtree)

        while len(lss) < ntasks:
            max_score = 0
            max_instance_index = -1
            max_ls = None
            max_split_id = 0

            for (split_id, ls) in lss:
                for instance_index, lk in enumerate(ls):
                    if lk.split_score > max_score:
                        max_score = lk.split_score
                        max_instance_index = instance_index
                        max_ls = ls
                        max_split_id = split_id

            if max_ls is None:
                raise RuntimeError("no more splits!")

            lss.remove((max_split_id, max_ls))
            lss += self._split_domtree(max_split_id, max_ls, max_instance_index, True)
        return lss

    def _split_domtree(self, split_id, ls, max_instance_index, find_best_domtree_split):
        lk = ls[max_instance_index]
        inst = self._instances[max_instance_index]
        nid = lk.domtree_node_id()
        split = lk.get_best_split()
        split_score = lk.split_score
        split_balance = lk.split_balance

        inst.subspaces.split(lk) # lk's fields are invalid after .split(lk)

        domtree = inst.subspaces.domtree()
        l, r = domtree.left(nid), domtree.right(nid)
        lk_l = inst.subspaces.get_subspace(l)
        lk_r = inst.subspaces.get_subspace(r)

        if find_best_domtree_split:
            lk_l.find_best_domtree_split(inst.addtree)
            lk_r.find_best_domtree_split(inst.addtree)

        split_id_l = self._new_split_id()
        split_id_r = self._new_split_id()
        ls_l = ls.copy(); ls_l[max_instance_index] = lk_l
        ls_r = ls.copy(); ls_r[max_instance_index] = lk_r

        # TODO re-check reachabilities in other trees due to new constraint
        # TODO add constraint to model

        self.results[split_id]["split"] = split
        self.results[split_id]["split_score"] = split_score
        self.results[split_id]["split_balance"] = split_balance
        self.results[split_id]["instance_index"] = inst.index
        self.results[split_id]["domtree_node_id"] = nid
        self.results[split_id]["next_split_ids"] = [split_id_l, split_id_r]

        self.results[split_id_l] = self._init_results(ls_l)
        self.results[split_id_r] = self._init_results(ls_r)
        self.results[split_id_l]["prev_split_id"] = split_id
        self.results[split_id_r]["prev_split_id"] = split_id

        self._print("SPLIT {}:{} {} into {}, {}, score {} ".format(
            inst.index, nid, lk.get_best_split(), l, r, split_score))

        return [(split_id_l, ls_l), (split_id_r, ls_r)]

    def _handle_done_future(self, f):
        t = f.result()
        status, check_time = t[0], t[1]

        self._print("{} for task {} in {:.2f}s (timeout={:.1f}s)".format(status,
            f.split_id, check_time, f.timeout))

        self.results[f.split_id]["status"] = status
        self.results[f.split_id]["check_time"] = check_time
        self.results[f.split_id]["split_id"] = f.split_id

        # We're finished with this branch!
        if status != Verifier.Result.UNKNOWN:
            self.done_count += 1
            model = t[2]
            self.results[f.split_id]["model"] = model
            if status.is_sat() and self._stop_when_sat_opt:
                self._stop_flag = True
            return []
        else:

            # We timed out, split and try again
            ls = t[2]
            next_timeout = min(self._timeout_max, self._timeout_rate * f.timeout)

            max_score = 0
            max_instance_index = -1
            for instance_index, lk in enumerate(ls):
                if lk.split_score > max_score:
                    max_score = lk.split_score
                    max_instance_index = instance_index

            new_ls = self._split_domtree(f.split_id, ls, max_instance_index, False)
            new_fs = [self._make_verify_future(sid, ls, next_timeout) for sid, ls in new_ls]

            return new_fs




    def _new_split_id(self):
        split_id = self._split_id
        self._split_id += 1
        return split_id

    def _make_verify_future(self, split_id, ls, timeout):
        f = self._client.submit(DistributedVerifier._verify_fun,
                self._addtrees_fut, ls, timeout,
                self._verifier_factory)
        f.timeout = timeout
        f.split_id = split_id
        self._split_id += 1
        return f

    def _init_results(self, ls):
        return {
            "num_unreachable": self._num_unreachable(ls),
            "bounds": self._tree_bounds(ls)
        }

    def _num_unreachable(self, ls):
        return sum(map(lambda lk: lk.num_unreachable(), ls))

    def _tree_bounds(self, ls):
        bounds = []
        for at, lk in zip(self._addtrees, ls):
            lo, hi = 0.0, 0.0
            for tree_index in range(len(at)):
                bnds = lk.get_tree_bounds(at, tree_index)
                lo += bnds[0]
                hi += bnds[1]
            bounds.append((lo, hi))
        return bounds

    def _print(self, msg):
        self._print_queue.append(msg)

    def _print_flush(self):
        for msg in self._print_queue:
            self._print_msg(msg)
        self._print_queue = []

    def _print_msg(self, msg):
        t = int(timeit.default_timer() - self.start_time)
        m, s = t // 60, t % 60
        h, m = m // 60, m % 60
        done = self.done_count
        rem = len(self._fs) if hasattr(self, "_fs") else -1
        print(f"[{h}h{m:02d}m{s:02d}s {done:>4} {rem:<4}]", msg)





    # - WORKERS ------------------------------------------------------------- #

    @staticmethod
    def _check_tree_paths(addtrees, ls, instance_index, tree_index, vfactory):
        addtree, l0 = addtrees[instance_index], ls[instance_index]
        tree = addtree[tree_index]
        stack = [(tree.root(), True)]
        v = vfactory(addtrees, ls)

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
    def _verify_fun(addtrees, ls, timeout, vfactory):
        v = vfactory(addtrees, ls)
        v.set_timeout(timeout)
        v.add_all_trees()
        try:
            status = v.check()
            model = {}
            if status.is_sat():
                model = v.model()
                model["family"] = v.model_family(model)

            return status, v.check_time, model

        except VerifierTimeout as e:
            print(f"timeout after {e.unk_after} (timeout = {timeout}) finding best split...")
            for instance_index, lk in enumerate(ls):
                if not lk.has_best_split():
                    lk.find_best_domtree_split(addtrees[instance_index])

            return Verifier.Result.UNKNOWN, v.check_time, ls
