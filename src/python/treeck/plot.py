# Copyright 2019 DTAI Research Group - KU Leuven.
# License: Apache License 2.0
# Author: Laurens Devos

import graphviz as gv
from . import LtSplit, BoolSplit

class TreePlot:

    def __init__(self, fontsize=10):
        self.g = gv.Graph(format="svg")
        self.g.attr(fontsize=str(fontsize), fontname="monospace")
        self.g.attr("node", fontsize=str(fontsize), fontname="monospace")
        self.index = 0

    def name(self, node):
        return "{}x{}".format(self.index, node)

    def nodes_eq(self, btree, stree, bnode, snode):
        return btree.is_internal(bnode) == stree.is_internal(snode) \
                and (not btree.is_internal(bnode) or \
                        (btree.get_split(bnode) == stree.get_split(snode))) \
                and (not btree.is_leaf(bnode) or \
                        (btree.get_leaf_value(bnode) == stree.get_leaf_value(snode)))

    def add_tree(self, tree,
            leaf_labels=None,
            feat_labels=None,
            plot_node_ids=False):
        g = gv.Graph()
        self.index += 1
        stack = [tree.root()]
        while len(stack) > 0:
            node = stack.pop()
            if tree.is_leaf(node):
                style = ""
                color = "black"
                if hasattr(tree, "get_leaf_value"):
                    s = "{:.3f}".format(tree.get_leaf_value(node))
                elif leaf_labels is not None and node in leaf_labels:
                    s = str(leaf_labels[node])
                    if s == "SAT": color = "darkgreen"
                    if s == "UNSAT": color = "red"
                    style = "bold"
                else:
                    s = f"l{node}"
                if plot_node_ids: s = f"{node}: {s}"
                g.node(self.name(node), s, style=style, color=color)
            else:
                split = tree.get_split(node)
                feat_id = split[1]
                s = ""
                if plot_node_ids: s = f"{node}:"
                g.node(self.name(node), f"{s} {self.get_split_label(split, feat_labels)}")
                stack.append(tree.right(node))
                stack.append(tree.left(node))

            if not tree.is_root(node):
                g.edge(self.name(tree.parent(node)), self.name(node))
        self.g.subgraph(g)

    def add_tree_cmp(self, btree, stree):
        self.index += 1
        stack = [(btree.root(), stree.root())] # (big node, small node)
        while len(stack) > 0:
            bnode, snode = stack.pop()
            if self.nodes_eq(btree, stree, bnode, snode):
                if btree.is_leaf(bnode):
                    self.g.node(self.name(bnode), "{:.3f}".format(btree.get_leaf_value(bnode)),
                            style="bold", color="darkgreen")
                else:
                    feat_id, split_value = btree.get_split(bnode)
                    self.g.node(self.name(bnode), "X{} < {:.3f}".format(feat_id, split_value),
                            style="bold", color="darkgreen")
                    stack.append((btree.right(bnode), stree.right(snode)))
                    stack.append((btree.left(bnode), stree.left(snode)))

                if not btree.is_root(bnode):
                    self.g.edge(self.name(btree.parent(bnode)), self.name(bnode),
                            style="bold", color="darkgreen")
            else:
                if btree.is_leaf(bnode):
                    self.g.node(self.name(bnode), "{:.3f}".format(btree.get_leaf_value(bnode)),
                            color="gray", fontcolor="gray")
                else:
                    feat_id, split_value = btree.get_split(bnode)
                    self.g.node(self.name(bnode), "X{} < {:.3f}".format(feat_id, split_value),
                            color="gray", fontcolor="gray")
                    stack.append((btree.right(bnode), snode))
                    stack.append((btree.left(bnode), snode))

                if not btree.is_root(bnode):
                    self.g.edge(self.name(btree.parent(bnode)), self.name(bnode), color="gray")

    def get_split_label(self, split, feat_labels=None):
        if isinstance(split, tuple): # its a domtree with splits (instance, type, feat_id, ...)
            instance = split[0]
            prefix = f"({instance}) "
            split = split[1]
        else: prefix = ""

        fname = f"X{split.feat_id}"
        if feat_labels is not None:
            fname = feat_labels[split.feat_id]
        if isinstance(split, LtSplit):
            return "{}{} < {:.3f}".format(prefix, fname, split.split_value)
        if isinstance(split, BoolSplit):
            return "{}{}".format(prefix, fname)

    def add_domtree_leaf(self, instance, tree, dtleaf):
        g = gv.Graph()
        self.index += 1
        stack = [(tree.root(), False)]
        while len(stack) > 0:
            node, marked = stack.pop()

            is_reachable = dtleaf.is_reachable(instance, tree.index(), node)
            marked |= not is_reachable
            c = "darkgreen" if not marked else "gray"
            s = "bold" if not marked else ""
            if not is_reachable: c = "red"

            if tree.is_leaf(node):
                g.node(self.name(node), "{:.3f}".format(tree.get_leaf_value(node)),
                        style=s, color=c, fontcolor=c)
            else:
                label = self.get_split_label(tree.get_split(node))
                g.node(self.name(node), label, style=s, color=c, fontcolor=c)
                stack.append((tree.right(node), marked))
                stack.append((tree.left(node), marked))

            if not tree.is_root(node):
                g.edge(self.name(tree.parent(node)), self.name(node), color=c)
        self.g.subgraph(g)

    def add_domains(self, domains):
        text = ""
        for i, dom in enumerate(domains):
            if dom.is_everything(): continue
            text += "X{}: {}\\l".format(i, dom)
        self.g.attr(label=text)

    def render(self, f):
        self.g.render(f)
