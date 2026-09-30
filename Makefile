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
#   make <mode>          one step and whatever it needs, e.g. `make candidates`
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
  normalize=service/normalize.py,service/indications.py,source/mesh_descriptors.py,source/moiety.py,dao/biokde.py,dao/tsv_repository.py \
  labels=service/section14.py,dao/tsv_repository.py \
  letters=service/letters.py,dao/tsv_repository.py \
  reviews=service/review_trials.py,service/review_role.py,service/label_role.py,service/approval_events.py,source/review_docs.py,source/trial_studies.py,source/raw_table.py,dao/tsv_repository.py \
  indication_normalize=service/indication_normalize.py,service/indications.py,service/letter_locate.py,service/approval_events.py,source/trial_studies.py,dao/biokde.py,dao/tsv_repository.py \
  graph_trials=service/graph_trials.py,service/review_trials.py,service/normalize.py,service/approval_events.py,source/trial_studies.py,dao/graph_evidence.py,dao/tsv_repository.py \
  graph_links=service/graph_links.py,service/indication_normalize.py,service/review_trials.py,service/normalize.py,service/approval_events.py,source/trial_studies.py,dao/graph_evidence.py,dao/tsv_repository.py \
  candidates=service/candidates.py,service/assemble.py,service/review_role.py,source/mesh_descriptors.py,dao/tsv_repository.py \
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
.PHONY: all status spine indications normalize labels letters reviews \
        indication-normalize graph-trials graph-links candidates assemble validate

all: $(OUT)/candidates.jsonl $(OUT)/trial_submission_map.tsv

# ---- P0 spine ---------------------------------------------------------------
$(OUT)/approval_events.tsv: $(CODE_approval_events) $(DRUGSFDA)
	$(MAIN) --mode approval-events

# ---- today's openFDA labels (the indication fallback) ------------------------
$(OUT)/indications.tsv $(OUT)/indication_text.tsv &: $(CODE_indications) $(LABELS_JSON)
	$(MAIN) --mode indications

# ---- P1 normalize -------------------------------------------------------------
$(OUT)/drug_map.tsv $(OUT)/indication_map.tsv $(OUT)/trial_drug_map.tsv \
$(OUT)/trial_condition_map.tsv &: $(CODE_normalize) $(OUT)/indications.tsv
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
$(OUT)/graph_trials.tsv: $(CODE_graph_trials) \
		$(OUT)/submission_indications.tsv $(OUT)/drug_map.tsv $(OUT)/indication_map.tsv
	$(MAIN) --mode graph-trials

$(OUT)/drug_only_trials.tsv: $(CODE_graph_links) \
		$(OUT)/submission_indications.tsv $(OUT)/drug_map.tsv $(OUT)/indication_map.tsv \
		$(OUT)/approval_events.tsv
	$(MAIN) --mode graph-links

# ---- P3 route + candidates (D35, D36) -------------------------------------------------
$(OUT)/candidates.jsonl $(OUT)/submission_routes.tsv &: $(CODE_candidates) \
		$(OUT)/submission_indications.tsv \
		$(OUT)/label_trials.tsv $(OUT)/graph_trials.tsv $(OUT)/drug_only_trials.tsv \
		$(OUT)/review_trial_mentions.tsv $(ADJ)/review_role_verdicts.jsonl \
		$(OUT)/trial_drug_map.tsv $(OUT)/trial_condition_map.tsv $(OUT)/drug_map.tsv
	$(MAIN) --mode candidates

# ---- P5 assemble ------------------------------------------------------------------------
$(OUT)/trial_submission_map.tsv $(OUT)/unlinked_submissions.tsv &: $(CODE_assemble) \
		$(OUT)/approval_events.tsv $(OUT)/candidates.jsonl $(OUT)/drug_map.tsv \
		$(OUT)/graph_trials.tsv $(OUT)/submission_indications.tsv \
		$(ADJ)/identify_verdicts.jsonl $(ADJ)/review_role_units.jsonl \
		$(ADJ)/review_role_verdicts.jsonl
	$(MAIN) --mode assemble

# ---- one step by its --mode name -------------------------------------------------------
spine: $(OUT)/approval_events.tsv
indications: $(OUT)/indications.tsv
normalize: $(OUT)/drug_map.tsv
labels: $(OUT)/label_trials.tsv
letters: $(OUT)/letter_indications.tsv
reviews: $(OUT)/review_trial_mentions.tsv
indication-normalize: $(OUT)/submission_indications.tsv
graph-trials: $(OUT)/graph_trials.tsv
graph-links: $(OUT)/drug_only_trials.tsv
candidates: $(OUT)/candidates.jsonl
assemble: $(OUT)/trial_submission_map.tsv

# P6 writes nothing make tracks; it is always run on the current outputs.
validate: $(OUT)/candidates.jsonl $(OUT)/trial_submission_map.tsv
	$(MAIN) --mode validate

status:
	@stale=$$($(MAKE) --no-print-directory -n all | grep -o -- '--mode [a-z-]*' | sort -u); \
	if [ -z "$$stale" ]; then echo "all outputs up to date"; \
	else echo "stale — make would run:"; echo "$$stale" | sed 's/^/  /'; fi
