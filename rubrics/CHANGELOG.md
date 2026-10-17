# Rubric changelog

Every change is made after reading DEV disagreements only. TEST is scored once, with the final
rubric, and never used for tuning.

## v1 (baseline)

Plain wording, one sentence per score point, no anchors, no rules. DEV results are in
`results/calibration_summary.json` (`calib_v1_dev_reason_first`).

## v1 -> v2: what the 20 largest DEV disagreements showed

Source: `results/disagreements_v1_dev.md`. In 17 of 20 the judge's reasoning was internally sensible;
the rubric left the deciding question open. Patterns, with the item ids that showed them:

1. **"No claims, so no errors" scored correctness 4.** Clarifying questions on clear requests,
   deflections and opinion dodges (`hs2-train-01124`, `-12527`, `-14451`) got correctness 4 while all
   annotators gave 0-1. HelpSteer2 defines correctness as *inclusion of all pertinent facts* without
   errors, so a reply with no pertinent facts is not correct. v1 never said so.
   -> rule: no pertinent facts = 0; definitions now say "pertinent facts".
2. **Explicit constraints were not checked.** "Use only eight syllables" (`-14855`, the line has 7 and
   breaks the -ark rhyme), "make this shorter" (`-10529`), dates for 2024 (`-09382`, several wrong),
   the "100 murderers" riddle (`-01480`, you become a murderer). The judge accepted the surface form.
   -> rule: list the task's constraints and check each literally; solve riddles/arithmetic yourself.
3. **Completeness ignored whether the content was right.** Wrong arithmetic (`-15036`), fabricated
   franchise figures (`-16501`), wrong Rubik's algorithm (`-15391`) and invalid matrix identities
   (`-12030`) all got completeness 4 because "every part was addressed". Annotators rate *helpfulness*,
   and a wrong answer does not help. -> rule: main content wrong caps completeness at 2.
4. **Impossible tasks.** "Summarize what you've read so far" with nothing provided (`hs2-validation-00818`):
   annotators gave 4 to the honest "I can't see prior text"; the judge gave 0-1.
   -> exception: honest explanation / right clarifying question on impossible or underspecified tasks = 3-4.
5. **Leniency.** Judge means were above human means on all three criteria (v1 DEV: correctness 2.40 vs
   2.05, completeness 2.57 vs 2.00, coherence 3.71 vs 3.32). -> "4 means an expert would change nothing".
6. **Coherence semantics.** Annotators give coherence 0 to replies that make no sense *as a reply*
   (anchor pool `hs2-train-13773`) and 4 to fluent fabrications (`hs2-train-15034`).
   -> coherence = clarity and logical flow as a reply, explicitly not truth.

Not fixable by wording (annotator noise): `hs2-train-14606` (a correct CSV-to-markdown conversion rated
[1, 0, 0, 3]) and the low human-human coherence agreement (see the human ceiling).

Anchors: one per score point for correctness and completeness and three for coherence (13 total),
paraphrased from `data/anchor_pool.jsonl` (HelpSteer2 items whose prompts are not in DEV or TEST,
annotator spread <= 1). The guide suggests 2-3 per point; one was used to stay inside the free-tier
token quota (each anchor adds ~50 prompt tokens to every judge call).
