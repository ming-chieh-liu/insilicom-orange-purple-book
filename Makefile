# Pipeline dependencies — the one place they are declared.
#
# Every target is a file a --mode writes; its prerequisites are the code that
# writes it and every file it reads. `make` rebuilds a file when anything it
# depends on is newer, transitively, so a code change can no longer leave a
# downstream file stale by accident (2026-09-30: submission_indications.tsv was
# written at 16:02, its code changed at 23:50, and P3 was re-run on it anyway).
#
#   make                 bring every no-agent output up to date
#   make status          list what is stale and the commands that would fix it
#   make <stage>         one stage of S1-S11 and whatever it needs (D38):
#                          spine drugs registry documents indications literature
#                          candidates assemble validate
#   make <mode>          the same by --mode name, e.g. `make indication-normalize`
#   make judge-prepare TASK=identify CASE=B|C|D|CD [LIMIT=n]
#                        TASK=review-roles | TASK=label-roles
#                        write the next agent batches; never judges (S6, S9)
#
# The dependency graph is drawn in docs/DESIGN.md §4.
#
# Agent verdicts (adjudication/*_verdicts.jsonl) are sources, not targets: they
# are produced by judges under a runbook, never by make. A new verdict makes
# the steps that read it stale, which is the point.
#
# FDA-fetching steps (labels, letters, reviews) run --offline here: make never
# touches the network. To fetch, run the --mode by hand, one downloader at a
# time, at 1 request/s (CLAUDE.md).

PY      ?= /data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
MAIN    := $(PY) main.py
OUT     := output
ADJ     := adjudication
RAW     := fda_raw
DRUGSFDA := $(RAW)/drugsfda/drugsatfda.zip
LABELS_JSON := $(wildcard $(RAW)/label/drug-label-*.json.zip)

STEPS := \
  approval_events=service/approval_events.py,dao/tsv_repository.py \
  indications=service/indications.py,dao/tsv_repository.py \
  normalize=service/normalize.py,service/drug_names.py,service/indications.py,source/mesh_descriptors.py,source/moiety.py,source/unii_bridge.py,source/unii_mesh.py,service/drug_identity.py,dao/ikraph2_entities.py,dao/biokde.py,dao/tsv_repository.py \
  labels=service/section14.py,dao/tsv_repository.py \
  letters=service/letters.py,dao/tsv_repository.py \
  reviews=service/review_trials.py,service/review_role.py,service/label_role.py,service/approval_events.py,source/review_docs.py,source/trial_studies.py,source/raw_table.py,dao/tsv_repository.py \
  indication_normalize=service/indication_normalize.py,service/indications.py,service/letter_locate.py,service/approval_events.py,source/trial_studies.py,dao/ikraph2_entities.py,dao/tsv_repository.py \
  graph_trials=service/graph_trials.py,service/review_trials.py,service/normalize.py,service/approval_events.py,source/trial_studies.py,dao/graph_evidence.py,dao/ikraph2_api.py,dao/tsv_repository.py \
  graph_links=service/graph_links.py,service/indication_normalize.py,service/review_trials.py,service/normalize.py,service/approval_events.py,source/trial_studies.py,dao/graph_evidence.py,dao/ikraph2_api.py,dao/tsv_repository.py \
  candidates=service/candidates.py,service/assemble.py,service/review_role.py,source/mesh_descriptors.py,source/unii_bridge.py,source/sponsor_families.py,dao/tsv_repository.py \
  assemble=service/assemble.py,service/candidates.py,service/candidate_match.py,service/identification.py,service/approval_events.py,dao/tsv_repository.py

# CODE_<step>: the step's entry modules (what main.py calls for that --mode)
# and every project module they import, generated from the import statements
# by utility/code_deps.py and regenerated whenever any module changes.
# When a mode starts calling a new module, add it to STEPS above.
PROJECT_PY := $(wildcard config/*.py dao/*.py service/*.py source/*.py utility/*.py)
.deps.mk: $(PROJECT_PY) Makefile
	$(PY) -m utility.code_deps $(STEPS) > $@
include .deps.mk

.DELETE_ON_ERROR:
.PHONY: all status spine drugs registry documents indications literature candidates \
        assemble validate judge-prepare openfda-labels normalize labels letters reviews \
        indication-normalize graph-trials graph-links

all: $(OUT)/candidates.jsonl $(OUT)/trial_submission_map.tsv

# ---- P0 spine ---------------------------------------------------------------
$(OUT)/approval_events.tsv: $(CODE_approval_events) $(DRUGSFDA)
	$(MAIN) --mode approval-events

# ---- today's openFDA labels (the indication fallback) ------------------------
$(OUT)/indications.tsv $(OUT)/indication_text.tsv &: $(CODE_indications) $(LABELS_JSON)
	$(MAIN) --mode indications

# ---- P1 normalize -------------------------------------------------------------
$(OUT)/drug_map.tsv $(OUT)/indication_map.tsv $(OUT)/trial_drug_map.tsv \
$(OUT)/trial_condition_map.tsv $(OUT)/drug_names.tsv $(OUT)/id_bridge.tsv \
$(OUT)/unii_mesh.tsv $(OUT)/drug_identity.tsv &: $(CODE_normalize) \
		$(OUT)/indications.tsv fda_raw/unii/UNII_Data.zip fda_raw/unii/UNIIs.zip \
		fda_raw/mesh/supp2026.xml
	$(MAIN) --mode normalize

# ---- P2a labels (cached PDFs only) -----------------------------------------------
$(OUT)/label_trials.tsv $(OUT)/submission_indication_text.tsv &: $(CODE_labels) \
		$(OUT)/approval_events.tsv
	$(MAIN) --mode labels --offline

# ---- P2b letters (cached PDFs only) ------------------------------------------------
$(OUT)/letter_indications.tsv: $(CODE_letters) $(OUT)/approval_events.tsv
	$(MAIN) --mode letters --offline

# ---- P2c reviews (cached PDFs only) ------------------------------------------------
$(OUT)/review_trial_mentions.tsv $(ADJ)/review_role_units.jsonl &: $(CODE_reviews) \
		$(OUT)/approval_events.tsv
	$(MAIN) --mode reviews --offline

# ---- P2i the one indication table (D30, D34) ---------------------------------------
$(OUT)/submission_indications.tsv: $(CODE_indication_normalize) \
		$(OUT)/letter_indications.tsv $(ADJ)/letter_indication_verdicts.jsonl \
		$(OUT)/submission_indication_text.tsv $(OUT)/label_trials.tsv \
		$(OUT)/indications.tsv $(OUT)/indication_text.tsv $(OUT)/trial_condition_map.tsv
	$(MAIN) --mode indication-normalize

# ---- P2g literature candidates (D35) --------------------------------------------------
$(OUT)/graph_trials.tsv $(OUT)/literature_abstracts.jsonl &: $(CODE_graph_trials) \
		$(OUT)/submission_indications.tsv $(OUT)/drug_map.tsv $(OUT)/indication_map.tsv
	$(MAIN) --mode graph-trials

$(OUT)/drug_only_trials.tsv $(OUT)/drug_only_abstracts.jsonl &: $(CODE_graph_links) \
		$(OUT)/submission_indications.tsv $(OUT)/drug_map.tsv $(OUT)/indication_map.tsv \
		$(OUT)/approval_events.tsv
	$(MAIN) --mode graph-links

# ---- P3 route + candidates (D35, D36) -------------------------------------------------
$(OUT)/candidates.jsonl $(OUT)/submission_routes.tsv &: $(CODE_candidates) \
		$(OUT)/submission_indications.tsv \
		$(OUT)/label_trials.tsv $(OUT)/graph_trials.tsv $(OUT)/drug_only_trials.tsv \
		$(OUT)/review_trial_mentions.tsv $(ADJ)/review_role_verdicts.jsonl \
		$(OUT)/trial_drug_map.tsv $(OUT)/trial_condition_map.tsv $(OUT)/drug_map.tsv \
		$(OUT)/drug_names.tsv $(OUT)/id_bridge.tsv
	$(MAIN) --mode candidates

# ---- P5 assemble ------------------------------------------------------------------------
$(OUT)/trial_submission_map.tsv $(OUT)/unlinked_submissions.tsv &: $(CODE_assemble) \
		$(OUT)/approval_events.tsv $(OUT)/candidates.jsonl $(OUT)/drug_map.tsv \
		$(OUT)/graph_trials.tsv $(OUT)/submission_indications.tsv \
		$(ADJ)/identify_verdicts.jsonl $(ADJ)/review_role_units.jsonl \
		$(ADJ)/review_role_verdicts.jsonl
	$(MAIN) --mode assemble

# ---- stages S1-S11 (D38) ------------------------------------------------------------------
spine: $(OUT)/approval_events.tsv                                              # S1
drugs: $(OUT)/drug_names.tsv $(OUT)/drug_map.tsv $(OUT)/id_bridge.tsv \
       $(OUT)/unii_mesh.tsv $(OUT)/drug_identity.tsv                          # S2
registry: $(OUT)/trial_drug_map.tsv $(OUT)/trial_condition_map.tsv             # S3
documents: $(OUT)/label_trials.tsv $(OUT)/letter_indications.tsv \
           $(OUT)/review_trial_mentions.tsv                                    # S4 (--offline)
indications: $(OUT)/submission_indications.tsv                                 # S5
#                                                                                S6 agents
literature: $(OUT)/graph_trials.tsv $(OUT)/drug_only_trials.tsv \
            $(OUT)/literature_abstracts.jsonl $(OUT)/drug_only_abstracts.jsonl   # S7
candidates: $(OUT)/candidates.jsonl                                            # S8
#                                                                                S9 agents
assemble: $(OUT)/trial_submission_map.tsv                                      # S10

# Agent batches for S6 / S9, on up-to-date inputs (main.py refuses stale ones).
TASK  ?= identify
CASE  ?= B
LIMIT ?=
judge-prepare:
ifeq ($(TASK),identify)
	$(MAIN) --mode match-prepare --case $(CASE) $(if $(LIMIT),--limit $(LIMIT))
else ifeq ($(TASK),review-roles)
	$(MAIN) --mode review-role-prepare
else ifeq ($(TASK),label-roles)
	$(MAIN) --mode label-role-prepare
else
	$(error TASK must be identify, review-roles or label-roles)
endif

# ---- the same steps by --mode name -------------------------------------------------------
openfda-labels: $(OUT)/indications.tsv          # --mode indications (today's openFDA labels)
normalize: $(OUT)/drug_map.tsv
labels: $(OUT)/label_trials.tsv
letters: $(OUT)/letter_indications.tsv
reviews: $(OUT)/review_trial_mentions.tsv
indication-normalize: $(OUT)/submission_indications.tsv
graph-trials: $(OUT)/graph_trials.tsv
graph-links: $(OUT)/drug_only_trials.tsv
candidates: $(OUT)/candidates.jsonl
assemble: $(OUT)/trial_submission_map.tsv

# S11 writes nothing make tracks; it is always run on the current outputs.
validate: $(OUT)/candidates.jsonl $(OUT)/trial_submission_map.tsv
	$(MAIN) --mode validate

status:
	@stale=$$(env -u MAKEFLAGS -u MFLAGS -u MAKELEVEL -u MAKE_RESTARTS $(MAKE) --no-print-directory -n all | grep -o -- '--mode [a-z-]*' | sort -u); \
	if [ -z "$$stale" ]; then echo "all outputs up to date"; \
	else echo "stale — make would run:"; echo "$$stale" | sed 's/^/  /'; fi
