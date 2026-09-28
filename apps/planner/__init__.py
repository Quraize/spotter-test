"""
Pure domain logic for fuel-stop planning. No Django imports anywhere in this package.

    corridor  - project stations onto a route: mile marker + detour for each candidate
    optimizer - choose where and how much to buy (provably optimal greedy)
"""
