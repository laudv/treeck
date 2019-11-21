#ifndef TREECK_SEARCHSPACE_H
#define TREECK_SEARCHSPACE_H

#include <functional>
#include <memory>
#include <unordered_map>
#include <vector>
#include <stack>

#include "domain.h"
#include "tree.h"

namespace treeck {

    struct LeafInfo {
        Split dom_split;
        double score;

        LeafInfo();
        LeafInfo(Split split, double score);

        template <typename Archive>
        void serialize(Archive& archive);
    };

    std::ostream& operator<<(std::ostream& s, LeafInfo inf);

    class SearchSpace {
    public:
        using DomTreeT = Tree<LeafInfo>;

        using SplitMap = std::unordered_map<FeatId, std::vector<double>>;
        using MeasureF = std::function<double(const SearchSpace&, const Domains&, LtSplit)>;
        using StopCondF = std::function<bool(const SearchSpace&)>;

    private:
        size_t num_features_;
        std::shared_ptr<const AddTree> addtree_;
        DomTreeT domtree_; // domain tree
        SplitMap splits_map_;
        std::vector<NodeId> leafs_;
        Domains root_domains_;
        Domains domains_;
        std::vector<double> scores_;

        void compute_best_score(NodeId domtree_leaf_id, MeasureF measure);

    public:
        SearchSpace(std::shared_ptr<const AddTree> addtree);
        SearchSpace(std::shared_ptr<const AddTree> addtree, const Domains& root_domains);

        size_t num_features() const;
        const AddTree& addtree() const;
        const DomTreeT& domtree() const;
        const std::vector<double>& scores() const;
        const std::vector<NodeId>& leafs() const;
        const Domains& root_domains() const;
        void get_domains(NodeId node_id, Domains& domains) const;

        void split(MeasureF measure, StopCondF cond);
    };

    struct UnreachableNodesMeasure {
        double operator()(
                const SearchSpace& sp,
                const Domains& domains,
                LtSplit split);

        int count_unreachable_nodes(
                const AddTree&,
                const Domains& parent_domains,
                FeatId feat_id,
                RealDomain new_domain);
    };

    struct NumDomTreeLeafsStopCond {
        size_t max_num_leafs;
        bool operator()(const SearchSpace& sp);
    };

} /* namespace treeck */

#endif /* TREECK_SEARCHSPACE_H */
