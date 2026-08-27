#!/usr/bin/env bash
# fetch_finra_members.sh — PARKED STUB. FINRA is not fetched in this series.
#
# FINRA (Financial Industry Regulatory Authority) member list was considered
# as an additional institution source for financial_institution entries.
#
# Only the FDIC and EDGAR institution sources ship; the FINRA member list is
# parked until its license is cleared under an approved source plan.
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
# This stub is kept so the script name is findable and the current posture is
# documented in-repo. Add FINRA in a follow-up PR only under an approved
# source plan that clears the license.

echo "FINRA member list fetch is NOT implemented (parked pending an approved source plan)."
echo ""
echo "Decision: SHIP FDIC + EDGAR ONLY."
echo ""
echo "FINRA is a private SRO. Member-list license is UNVERIFIED."
echo "License text location: https://www.finra.org/about/firms-we-regulate"
echo "License status: not on the SOURCES.md allowlist."
echo ""
echo "To add FINRA in a future session:"
echo "  1. Verify the member-list license against the SOURCES.md allowlist under an approved source plan."
echo "  2. If on the allowlist, implement this script following fetch_fdic_banks.sh."
echo "  3. Add parse_finra.py and wire into build.py."
exit 2
