#ifndef TREECK_DOMAIN_H
#define TREECK_DOMAIN_H

#include <tuple>
#include <ostream>
#include <vector>

namespace treeck {

    using FloatT = float;

    /**          lo                  hi
     *           [--- real domain ---)
     * ---X1--------------x2-----------------x3-----------> (real axis)
     *
     * x1 -> LEFT:      value not in domain and smaller than any value in the domain
     * x2 -> IN_DOMAIN: value in domain
     * x3 -> RIGHT:     value not in domain and larger than any value in the domain (also value==hi)
     */
    enum WhereFlag {
        LEFT      = -1,  // x1: value lies to the left of the domain
        IN_DOMAIN = 0,   // x2: value lies in the domain
        RIGHT     = 1    // x3: value lies to the right of the domain
    };

    /** An interval [lo, hi) */
    struct RealDomain {
        FloatT lo, hi;

        RealDomain();
        RealDomain(FloatT lo, FloatT hi);
        
        bool is_everything() const;
        WhereFlag where_is(FloatT value) const;
        WhereFlag where_is_strict(FloatT value) const;
        bool contains(FloatT value) const;
        bool contains_strict(FloatT value) const;
        bool overlaps(const RealDomain& other) const;
        bool covers(const RealDomain& other) const;
        bool covers_strict(const RealDomain& value) const;
        std::tuple<RealDomain, RealDomain> split(FloatT value) const;
    };

    std::ostream& operator<<(std::ostream& s, const RealDomain& d);

    // TODO remove
    class Domains {
    public:
        using vec_t = std::vector<RealDomain>;
        using iterator = typename vec_t::iterator;
        using const_iterator = typename vec_t::const_iterator;

    private:
        vec_t domains_;

    public:
        Domains();
        Domains(vec_t domains);
        size_t size() const;
        void resize(size_t size);

        iterator begin();
        iterator end();
        const_iterator begin() const;
        const_iterator end() const;
        const_iterator cbegin() const;
        const_iterator cend() const;

        const RealDomain& operator[](size_t i) const;
        RealDomain& operator[](size_t i);

        const vec_t& vec() const;
    };

} /* namespace treeck */

#endif /* TREECK_DOMAIN_H */
