# Legacy figures

These four images were produced by an earlier version of `new-file-pso-asvm.py` that is
no longer present in this repository. The current script (see `../../new-file-pso-asvm.py`)
only regenerates the 8 figures in `pso_results/` (renamed to match the manuscript's
Figure 5–9/11/13/14 numbering) — it does not reproduce these.

They are kept here, under their closest-matching manuscript figure number, for reference:

| File | Closest manuscript figure |
|------|---------------------------|
| `Fig10_ROC_PR_Curves.png` | Figure 10 — ROC and Precision-Recall analysis |
| `Fig12_Learning_Curve.png` | Figure 12 — Learning curve analysis |
| `Fig11_CV_Folds_alt.png` | Figure 11 — 10-fold stratified CV accuracy (alternate/earlier render) |
| `Fig14_Model_Comparison_alt.png` | Figure 14 — Five-fold CV F1-macro baseline comparison (alternate/earlier render) |

`Fig11_CV_Folds_alt.png` and `Fig14_Model_Comparison_alt.png` cover the same content as
`../Fig11_CV_Folds.png` and `../Fig14_Model_Comparison.png` respectively, just from a
different run of the (now superseded) script version — kept in case the rendering differs
in a way that matters.

Manuscript Figure 15 (per-feature impact/importance) has no corresponding file anywhere in
this repository and is not currently reproducible from the included code.
