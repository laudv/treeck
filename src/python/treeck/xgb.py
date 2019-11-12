import json

from xgboost.sklearn import XGBModel
from xgboost.core import Booster

from . import LtSplit, AddTree

def addtree_from_xgb_model(model):
    base_score = 0.5
    if isinstance(model, XGBModel):
        base_score = model.base_score
        model = model.get_booster()
    assert isinstance(model, Booster)

    dump = model.get_dump("", dump_format="json")
    at = AddTree()
    at.base_score = base_score

    for tree_dump in dump:
        _parse_tree(at, tree_dump)

    return at
    
def _parse_tree(at, tree_dump):
    tree = at.add_tree()
    stack = [(tree.root(), json.loads(tree_dump))]

    while len(stack) > 0:
        node, node_json = stack.pop()
        if "leaf" not in node_json:
            feat_id = int(node_json["split"][1:])
            split_value = node_json["split_condition"]
            node.split(LtSplit(feat_id, split_value))

            # let's hope the ordering of "children" is [left,right]
            stack.append((node.right(), node_json["children"][1]))
            stack.append((node.left(), node_json["children"][0]))
        else:
            leaf_value = node_json["leaf"]
            node.set_leaf_value(leaf_value)
