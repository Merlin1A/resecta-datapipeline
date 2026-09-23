"""The three routing tables: artifact -> schema, artifact -> install target, shrink-guarded routes.

One data module; the CLI re-exports the names for their importers.
"""

from __future__ import annotations

# Mapping from artifact relative-path pattern to schema name.
# Populated per phase as artifacts land.
SCHEMA_ROUTES: dict[str, str] = {
    "vectors/npi_test_vectors.json": "npi_test_vectors",
    "vectors/dea_test_vectors.json": "dea_test_vectors",
    "vectors/ssn_structural_vectors.json": "ssn_structural_vectors",
    "vectors/credit_card_vectors.json": "credit_card_vectors",
    "vectors/ein_vectors.json": "ein_vectors",
    "vectors/itin_vectors.json": "itin_vectors",
    "vectors/dob_vectors.json": "dob_vectors",
    # Follow-up vectors (Phone, Email, Passport, DL, MRN, Bates, LicensePlate).
    "vectors/phone_test_vectors.json": "phone_vectors",
    "vectors/email_test_vectors.json": "email_vectors",
    "vectors/passport_test_vectors.json": "passport_vectors",
    "vectors/drivers_license_test_vectors.json": "drivers_license_vectors",
    "vectors/mrn_test_vectors.json": "mrn_vectors",
    "vectors/bates_test_vectors.json": "bates_vectors",
    "vectors/license_plate_test_vectors.json": "license_plate_vectors",
    # ABA routing-number vectors.
    "vectors/routing_number_vectors.json": "routing_number_vectors",
    "gazetteers/zip_scf_states.json": "zip_scf_states",
    "fuzz/redos_payloads.json": "redos_payloads",
    # T4.3 malformed-PDF fixtures. Schema-routed but deliberately absent from
    # INSTALL_ROUTES: the set is a development input to the H4.2 robustness
    # runner and never ships in the app bundle (same posture as
    # g8_detection_baseline / g8_headroom).
    "fuzz/pdf_mutations.json": "pdf_mutations",
    "adversarial/adversarial_patterns.json": "adversarial_patterns",
    # Phase 2
    "gazetteers/gazetteer_manifest.json": "gazetteer_manifest",
    "gazetteers/gazetteer_manifest.shipped.json": "gazetteer_manifest",
    "gazetteers/name_filters.cutover-diff.json": "cutover_diff",
    "gazetteers/negative_context_candidates.json": "negative_context",
    "gazetteers/negative_context.json": "negative_context",
    "gazetteers/institutions.json": "institutions",
    "gazetteers/institutions.cutover-diff.json": "cutover_diff",
    "gazetteers/address_components.json": "address_components",
    "gazetteers/address_components.cutover-diff.json": "cutover_diff",
    # Nickname/diminutive sidecar; built only once the
    # CC0 raw source has been fetched (see the Makefile GAZ_NICKNAMES gate).
    "gazetteers/nicknames.json": "nicknames",
    # Common-word curation sidecar for the surname Bloom filter (read by the
    # Swift NameGazetteer; the filters themselves are untouched).
    "gazetteers/name_common_words.json": "name_common_words",
    "gazetteers/dl_patterns.json": "dl_patterns",
    "gazetteers/passport_patterns.json": "passport_patterns",
    "context/context_keywords.json": "context_keywords",
    "rules/rule_catalog.json": "rule_catalog",
    "demographics/coverage_report.json": "demographic_coverage",
    "demographics/g8_bucket_recall_v1.json": "g8_bucket_recall",
    "instrumentation/bundle_size.json": "bundle_size",
    # Phase 3
    "classifier/doctype_keywords.json": "doctype_keywords",
    "classifier/preset_thresholds_candidates.json": "preset_thresholds",
    # In-band context scorer. Both the candidate and the final-named
    # build artifact validate against the same schema; only the final is routed
    # for install (below); promotion happens under an approved change plan.
    "classifier/context_scorer.json": "context_scorer",
    "classifier/context_scorer_candidates.json": "context_scorer",
    "corpus/g8_corpus.json": "g8_corpus",
    # Generator PROFILES (Spec-C name-context variety, Spec-D
    # furniture density, both; Spec-A name forms, Spec-G locale axis, Spec-H
    # sparse placeholder, all three): the same 1,100 documents re-rendered; built
    # by `build corpus g8 --profile <p>`, validated against the same schema,
    # never installed (the engine harness reads one through the test-target
    # override RESECTA_G8_CORPUS_PATH under `make eval EVAL_CORPUS_PROFILE=`).
    "corpus/g8_corpus_g8-specC.json": "g8_corpus",
    "corpus/g8_corpus_g8-specD.json": "g8_corpus",
    "corpus/g8_corpus_g8-specCD.json": "g8_corpus",
    # Spec-A name forms, Spec-G locale axis, Spec-H sparse placeholder, all three.
    "corpus/g8_corpus_g8-specA.json": "g8_corpus",
    "corpus/g8_corpus_g8-specG.json": "g8_corpus",
    "corpus/g8_corpus_g8-specH.json": "g8_corpus",
    "corpus/g8_corpus_g8-specAGH.json": "g8_corpus",
    # Eval baseline — deterministic no-PII negative corpus for the
    # document-level FP measurement. Dev/eval fixture; no INSTALL_ROUTES entry
    # (not shipped), like g8_bucket_recall.
    "corpus/negative_corpus.json": "negative_corpus",
    # Eval baseline — derived detection baseline + learned-term headroom probe.
    # Produced by `resecta-data build eval-baseline` from the Swift harness's
    # _cells.json / _raw_scores.json. Dev/eval only; no INSTALL_ROUTES entry
    # (not shipped), like g8_bucket_recall / negative_corpus.
    "eval/g8_detection_baseline.json": "g8_detection_baseline",
    "eval/g8_headroom.json": "g8_headroom",
    # Per-span outcome aggregate from the emitters' JSONL sidecars (offsets
    # only); same dev/eval posture as the two rows above.
    "eval/g8_span_outcomes.json": "g8_span_outcomes",
    # The Site-B minus detector-site join of two derived baselines (the
    # site-gap arithmetic). Dev/eval only; no INSTALL_ROUTES entry.
    "eval/g8_site_gap.json": "g8_site_gap",
    # The four-clause comparator over documents_eval.json rows.
    "eval/g8_compare_documents_verdict.json": "g8_compare_documents",
    # Phase 3b (produced only when Swift-side dumps are present under
    # build/calibration/).
    "classifier/doctype_temperature.json": "doctype_temperature",
    "classifier/preset_thresholds.json": "preset_thresholds",
}


# Mapping from build/-relative artifact path to (target, subpath) where
# target is "resources" (shipped to Swift Resources/) or "fixtures" (shipped
# to Swift Tests/.../Fixtures/). Artifacts without an entry stay in build/.
#
# Phase 2 ships the dual-Bloom filter bundle to Resources/Gazetteers/. The
# negative-context candidate file and the demographic coverage report stay
# in build/: the former is replaced by the reviewed file staged from reviewed/, the latter is
# a dev/CI artifact not shipped to end users.
INSTALL_ROUTES: dict[str, tuple[str, str]] = {
    "vectors/npi_test_vectors.json": ("fixtures", "vectors/npi_test_vectors.json"),
    "vectors/dea_test_vectors.json": ("fixtures", "vectors/dea_test_vectors.json"),
    "vectors/ssn_structural_vectors.json": ("fixtures", "vectors/ssn_structural_vectors.json"),
    "vectors/credit_card_vectors.json": ("fixtures", "vectors/credit_card_vectors.json"),
    "vectors/ein_vectors.json": ("fixtures", "vectors/ein_vectors.json"),
    "vectors/itin_vectors.json": ("fixtures", "vectors/itin_vectors.json"),
    "vectors/dob_vectors.json": ("fixtures", "vectors/dob_vectors.json"),
    "vectors/phone_test_vectors.json": ("fixtures", "vectors/phone_test_vectors.json"),
    "vectors/email_test_vectors.json": ("fixtures", "vectors/email_test_vectors.json"),
    "vectors/passport_test_vectors.json": ("fixtures", "vectors/passport_test_vectors.json"),
    "vectors/drivers_license_test_vectors.json": (
        "fixtures",
        "vectors/drivers_license_test_vectors.json",
    ),
    "vectors/mrn_test_vectors.json": ("fixtures", "vectors/mrn_test_vectors.json"),
    "vectors/bates_test_vectors.json": ("fixtures", "vectors/bates_test_vectors.json"),
    "vectors/license_plate_test_vectors.json": (
        "fixtures",
        "vectors/license_plate_test_vectors.json",
    ),
    # ABA routing-number vectors.
    "vectors/routing_number_vectors.json": ("fixtures", "vectors/routing_number_vectors.json"),
    "fuzz/redos_payloads.json": ("fixtures", "fuzz/redos_payloads.json"),
    "adversarial/adversarial_patterns.json": (
        "fixtures",
        "adversarial/adversarial_patterns.json",
    ),
    "gazetteers/surnames.bloom": ("resources", "Gazetteers/surnames.bloom"),
    "gazetteers/given-names.bloom": ("resources", "Gazetteers/given-names.bloom"),
    # The bundle's manifest is the SHIPPED manifest: the bloom builder's
    # `gazetteer_manifest.json` (locked, a `make build` product, held in
    # build/) plus the `assets[]` digests `make manifest-assets` derives at
    # install time. Only the shipped file is routed, so one dest has one source.
    "gazetteers/gazetteer_manifest.shipped.json": (
        "resources",
        "Gazetteers/gazetteer-manifest.json",
    ),
    # Signed manifest peer files. The .sig is the detached Ed25519
    # signature over the shipped manifest; the .pem is the public key
    # the iOS engine verifies against. Both produced by `make sign-manifest`
    # (a `make install-assets` prerequisite).
    "gazetteers/gazetteer_manifest.sig": ("resources", "Gazetteers/gazetteer_manifest.sig"),
    "gazetteers/manifest_public_key.pem": ("resources", "Gazetteers/manifest_public_key.pem"),
    "gazetteers/dl_patterns.json": ("resources", "Gazetteers/dl_patterns.json"),
    "gazetteers/passport_patterns.json": ("resources", "Gazetteers/passport_patterns.json"),
    # Phase 3 AddressDetector landed (ZIPStateTableLoader.swift consumes this
    # at runtime); zip_scf_states ships as a runtime resource. The
    # iOS copy was previously manually placed — install-assets now owns it.
    "gazetteers/zip_scf_states.json": ("resources", "Gazetteers/zip_scf_states.json"),
    "gazetteers/negative_context.json": ("resources", "Gazetteers/negative-context.json"),
    # institutions and address_components were previously MANUAL copies in
    # the iOS tree; install-assets now owns them. The first routed install
    # reconciles drifted iOS copies — review the install diff.
    # nicknames.json is the new given-name sidecar; the route is inert until
    # the artifact exists in build/ (post-fetch).
    "gazetteers/institutions.json": ("resources", "Gazetteers/institutions.json"),
    "gazetteers/address_components.json": ("resources", "Gazetteers/address_components.json"),
    "gazetteers/nicknames.json": ("resources", "Gazetteers/nicknames.json"),
    # Common-word curation sidecar; hyphenated on the iOS side like the other
    # runtime gazetteer files.
    "gazetteers/name_common_words.json": ("resources", "Gazetteers/name-common-words.json"),
    "context/context_keywords.json": ("resources", "Gazetteers/context-keywords.json"),
    "rules/rule_catalog.json": ("resources", "Audit/rule-catalog.json"),
    # Phase 3: doctype keywords ship as a runtime resource.
    # Preset-threshold candidates stay in build/ pending the Phase 3b G9
    # sweep. The G8 corpus is a test fixture.
    "classifier/doctype_keywords.json": ("resources", "Classifier/doctype-keywords.json"),
    # The context scorer ships hyphenated to Classifier/. FINAL only; the
    # `_candidates` artifact stays in build/. shutil.copy2 does no auto-rename,
    # so the hyphenated dest is hard-coded here. The installed promotion
    # happens under an approved change plan.
    "classifier/context_scorer.json": ("resources", "Classifier/context-scorer.json"),
    "corpus/g8_corpus.json": ("fixtures", "corpus/g8_corpus.json"),
    # Phase 3b: calibrated artifacts ship to Classifier/. Only present when
    # `make calibrate` has been run against Swift-produced dumps. The
    # placeholder `*_candidates.json` stays in build/ and is not routed.
    "classifier/doctype_temperature.json": ("resources", "Classifier/doctype-temperature.json"),
    "classifier/preset_thresholds.json": ("resources", "Classifier/preset-thresholds.json"),
}


# Routes whose JSON `entries` list must not silently shrink on install. A
# committed shipped file may be an out-of-band superset (e.g. institutions.json
# built on Linux from the full FDIC / federal-agency sources, committed in #27);
# a bare build/ draft must not regress it. See D11-config-golive-F1 / REL-5.
SHRINK_GUARDED_ROUTES: frozenset[str] = frozenset(
    {
        "gazetteers/institutions.json",
    }
)
