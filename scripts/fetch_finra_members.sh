#!/usr/bin/env bash
# fetch_finra_members.sh — PARKED STUB. FINRA decided OUT 2026-06-10.
#
# FINRA (Financial Industry Regulatory Authority) member list was considered
# as an additional institution source for financial_institution entries.
#
# Decision (Jesse, 2026-06-10): SHIP FDIC + EDGAR ONLY THIS SERIES.
#
# Rationale: FINRA is a private SRO (self-regulatory organization). Its member
# list may require a license that is not on the SOURCES.md allowlist.
# The major broker-dealers — Fidelity, Schwab, Vanguard —
# are captured by the EDGAR top-500 anyway; FINRA only adds smaller
# broker-dealers. License verification is a legal-review touchpoint that is
# deferred to a future session.
#
# License text location: https://www.finra.org/about/firms-we-regulate
# Member list licensing: UNVERIFIED — not on the SOURCES.md allowlist.
#
# This stub is kept so the script name is findable and the decision is
# documented in-repo. Add FINRA in a follow-up PR only after Jesse confirms
# the license.

echo "FINRA member list fetch is NOT implemented (decided OUT 2026-06-10)."
echo ""
echo "Decision: SHIP FDIC + EDGAR ONLY."
echo ""
echo "FINRA is a private SRO. Member-list license is UNVERIFIED."
echo "License text location: https://www.finra.org/about/firms-we-regulate"
echo "License status: not on the CLAUDE.md §2.1 allowlist."
echo ""
echo "To add FINRA in a future session:"
echo "  1. Jesse verifies the member-list license against CLAUDE.md §2.1."
echo "  2. If on the allowlist, implement this script following fetch_fdic_banks.sh."
echo "  3. Add parse_finra.py and wire into build.py."
exit 2
