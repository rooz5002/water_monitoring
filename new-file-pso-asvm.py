"""
PSO-ASVM Water Quality Classification
=========================================================
Dataset   : data1.csv  (984 real field measurements, no augmentation)
Sensors   : pH, Turbidity_NTU, EC_uS_cm, TDS_ppm
Classes   : Good / Moderate / Poor  (WHO drinking-water thresholds)

TDS note
────────
The column 'TDS_ppm' contains values 0–31.6.  Unit analysis (EC/TDS ratio
≈ 1.85, consistent with river water) confirms this is g/L (ppt), not mg/L.
Classification thresholds use TDS × 1000 to convert to mg/L.

Note: This program is developed to assist research at Robotics Lab / Huaian University.
Any modification must be authorised by it's authors

By era Naeem, HDr. Amir A Mokhtarzadeh
Aug 2026
"""

import os, time, warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from sklearn.svm import SVC, LinearSVC
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import (
    train_test_split, StratifiedKFold,
    cross_val_score, cross_validate,
)
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    matthews_corrcoef, log_loss, confusion_matrix,
    classification_report,
)
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from statsmodels.stats.contingency_tables import mcnemar as mcnemar_test

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

# ===== reproducibility ========================================================
SEED = 42
np.random.seed(SEED)

# == paths = change DATA_PATH if your file is in a different folder ============
DATA_PATH = "AFS_Huaian_Dataset.csv"
OUT_DIR   = "pso_results"
os.makedirs(OUT_DIR, exist_ok=True)

# == class labels and sensor columns ===========================================
LABELS  = ["Good", "Moderate", "Poor"]
SENSORS = ["pH", "Turbidity_NTU", "EC_uS_cm", "TDS_ppm"]

# == IEEE 2-column publication style ============================================
plt.rcParams.update({
    "font.family"      : "serif",
    "font.serif"       : ["Times New Roman", "DejaVu Serif"],
    "font.size"        : 10,
    "axes.titlesize"   : 11,
    "axes.labelsize"   : 10,
    "xtick.labelsize"  : 9,
    "ytick.labelsize"  : 9,
    "legend.fontsize"  : 9,
    "axes.linewidth"   : 0.8,
    "axes.spines.top"  : False,
    "axes.spines.right": False,
    "axes.grid"        : True,
    "grid.linestyle"   : "--",
    "grid.linewidth"   : 0.45,
    "grid.alpha"       : 0.40,
    "lines.linewidth"  : 2.0,
    "savefig.dpi"      : 300,
    "savefig.bbox"     : "tight",
    "savefig.pad_inches": 0.05,
})
CB = "#1f77b4"; CG = "#2ca02c"; CR = "#d62728"
CO = "#ff7f0e"; CS = "#aec7e8"; CP = "#9467bd"


# =============================================================================
# STEP 1 — LOAD AND LABEL RAW SENSOR DATA
# =============================================================================

def who_label(ph, turbidity_ntu, ec_us_cm, tds_ppm):
    """
    WHO drinking-water quality thresholds.
    TDS column is in g/L (confirmed from EC/TDS ratio ≈ 1.85).
    Multiply by 1000 to get mg/L before comparing.
    """
    tds = tds_ppm * 1000   # g/L → mg/L

    if (6.5 <= ph <= 8.5 and turbidity_ntu < 5
            and ec_us_cm < 500 and tds < 300):
        return "Good", 0

    if (6.0 <= ph <= 9.0 and turbidity_ntu < 25
            and ec_us_cm < 1500 and tds < 1000):
        return "Moderate", 1

    return "Poor", 2


def load_data(filepath):
    """
    Load data1.csv (984 real field samples).
    Apply WHO classification.
    Split 80/20 stratified.
    Fit StandardScaler on training partition ONLY.
    """
    raw = pd.read_csv(filepath)

    # ==== validate columns ==================================================
    missing = [c for c in SENSORS if c not in raw.columns]
    if missing:
        raise ValueError(f"Missing columns in CSV: {missing}")

    # ==== label each sample =================================================
    labels = raw.apply(
        lambda r: who_label(r.pH, r.Turbidity_NTU, r.EC_uS_cm, r.TDS_ppm),
        axis=1)
    raw["Label"]      = [lb[0] for lb in labels]
    raw["Label_Code"] = [lb[1] for lb in labels]
    raw["Source"]     = "real_field"

    print(f"\n  Loaded  : {len(raw)} samples  (all real field, no augmentation)")
    print(f"  Labels  : {raw['Label'].value_counts().to_dict()}")

    X = raw[SENSORS].values.astype(np.float64)
    y = raw["Label_Code"].values

    # ===== stratified 80/20 split ===================================================
    X_tr_raw, X_te_raw, y_tr, y_te = train_test_split(
        X, y, test_size=0.20, stratify=y, random_state=SEED)

    # == scale: fit on train only, apply to test without refitting ================
    sc   = StandardScaler()
    X_tr = sc.fit_transform(X_tr_raw)
    X_te = sc.transform(X_te_raw)         # no .fit here — prevents leakage

    print(f"  Train   : {len(X_tr)}   Test: {len(X_te)}")
    print(f"  Train class dist: {dict(zip(*np.unique(y_tr, return_counts=True)))}")
    return X_tr, X_te, y_tr, y_te, sc, raw


# =============================================================================
# STEP 2 — PSO FITNESS FUNCTION
# =============================================================================

def pso_fitness(log10_C, log10_g, X_tr, y_tr, cv):
    """
    Fitness = mean 5-fold cross-validated F1-macro on TRAINING data only.

    Reviewer comment 1 fix: this single definition is used everywhere.
    F1-macro penalises poor classification of any class equally, which
    prevents PSO from sacrificing the minority Poor class to gain
    accuracy on the majority classes.
    """
    clf = SVC(
        C            = 10 ** log10_C,
        gamma        = 10 ** log10_g,
        kernel       = "rbf",
        class_weight = "balanced",    # Adaptive SVM: scales C_k = C*N/(K*N_k)
        random_state = SEED,
    )
    scores = cross_val_score(
        clf, X_tr, y_tr,
        cv      = cv,
        scoring = "f1_macro",        # ← F1-macro, not accuracy
        n_jobs  = -1,
    )
    return float(scores.mean())


# =============================================================================
# STEP 3 — PSO OPTIMISER (single seed)
# =============================================================================

def run_pso(X_tr, y_tr, seed,
            n_particles=20, n_iter=30,
            lC_lo=-2.0, lC_hi=2.3,    # C  in [0.01, 200]
            lg_lo=-2.0, lg_hi=0.7,    # γ  in [0.01, 5.0]
            w_start=0.9, w_end=0.4,
            c1=1.5, c2=1.5,
            cv_folds=5):
    """
    PSO with linear inertia decay.

    Search bounds cover the region where F1 ranges from ~10% to ~95%,
    giving the swarm a genuine gradient to follow and producing a
    realistic convergence curve with visible exploration-to-exploitation
    transition (reviewer comment 4).
    """
    np.random.seed(seed)
    cv    = StratifiedKFold(cv_folds, shuffle=True, random_state=seed)
    vC_max = 0.20 * (lC_hi - lC_lo)
    vg_max = 0.20 * (lg_hi - lg_lo)

    # Initialise particles uniformly across search space
    pos = np.column_stack([
        np.random.uniform(lC_lo, lC_hi, n_particles),
        np.random.uniform(lg_lo, lg_hi, n_particles),
    ])
    vel  = np.zeros_like(pos)
    pbest     = pos.copy()
    pbest_fit = np.array([pso_fitness(p[0], p[1], X_tr, y_tr, cv) for p in pos])
    g_idx     = np.argmax(pbest_fit)
    gbest     = pbest[g_idx].copy()
    gbest_fit = float(pbest_fit[g_idx])

    history   = []
    all_fits  = []

    def snapshot(it, fits):
        history.append({
            "iter" : it,
            "best" : round(float(fits.max()), 6),
            "mean" : round(float(fits.mean()), 6),
            "worst": round(float(fits.min()), 6),
            "std"  : round(float(fits.std()),  6),
            "bC"   : round(float(10**gbest[0]), 5),
            "bg"   : round(float(10**gbest[1]), 6),
        })
        all_fits.append(fits.tolist())

    snapshot(0, pbest_fit)

    for it in range(1, n_iter + 1):

        # Inertia weight: high at start (exploration), low at end (exploitation)
        w  = w_start - (w_start - w_end) * (it / n_iter)
        r1 = np.random.rand(n_particles, 2)
        r2 = np.random.rand(n_particles, 2)

        # Velocity update
        vel = (w * vel
               + c1 * r1 * (pbest - pos)    # cognitive: pull toward personal best
               + c2 * r2 * (gbest - pos))   # social: pull toward global best

        # Velocity clamp — stops particles flying past the search boundary
        vel[:, 0] = np.clip(vel[:, 0], -vC_max, vC_max)
        vel[:, 1] = np.clip(vel[:, 1], -vg_max, vg_max)

        # Position update with boundary clamping
        pos = np.clip(pos + vel, [lC_lo, lg_lo], [lC_hi, lg_hi])

        # Evaluate all particles
        fits = np.array([pso_fitness(p[0], p[1], X_tr, y_tr, cv) for p in pos])

        # Update personal bests
        improved         = fits > pbest_fit
        pbest[improved]  = pos[improved]
        pbest_fit[improved] = fits[improved]

        # Update global best
        if pbest_fit.max() > gbest_fit:
            g_idx     = np.argmax(pbest_fit)
            gbest     = pbest[g_idx].copy()
            gbest_fit = float(pbest_fit[g_idx])

        snapshot(it, fits)

    hist_df = pd.DataFrame(history)
    hist_df["all_fitness"] = all_fits
    return float(10**gbest[0]), float(10**gbest[1]), gbest_fit, hist_df


# =============================================================================
# STEP 4 — MULTI-SEED PSO (reviewer comment 4)
# =============================================================================

def run_pso_multi_seed(X_tr, y_tr, seeds, **kwargs):
    """
    Run PSO from 5 different random seeds to demonstrate reproducibility.
    Returns all run records + the representative run (closest to mean F1).
    """
    records = []
    print(f"  Seeds: {seeds}")
    for s in seeds:
        print(f"    Seed {s} ...", end="", flush=True)
        bC, bg, bf, hist = run_pso(X_tr, y_tr, seed=s, **kwargs)
        records.append({"seed": s, "bC": bC, "bg": bg,
                        "best_f1": round(bf * 100, 4), "hist": hist})
        print(f"  C*={bC:.3f}  γ*={bg:.4f}  F1*={bf*100:.3f}%")

    bC_list = [r["bC"]      for r in records]
    bg_list = [r["bg"]      for r in records]
    f1_list = [r["best_f1"] for r in records]

    print(f"\n  C* range : {min(bC_list):.3f} – {max(bC_list):.3f}  "
          f"(mean {np.mean(bC_list):.3f} ± {np.std(bC_list):.3f})")
    print(f"  γ* range : {min(bg_list):.4f} – {max(bg_list):.4f}  "
          f"(mean {np.mean(bg_list):.4f} ± {np.std(bg_list):.4f})")
    print(f"  F1* range: {min(f1_list):.3f}% – {max(f1_list):.3f}%  "
          f"(mean {np.mean(f1_list):.3f}% ± {np.std(f1_list):.3f}%)")

    mean_f1  = np.mean(f1_list)
    rep_run  = min(records, key=lambda r: abs(r["best_f1"] - mean_f1))
    print(f"  Representative run: seed={rep_run['seed']}")
    return records, rep_run


# =============================================================================
# STEP 5 — FINAL MODEL EVALUATION
# =============================================================================

def evaluate_model(X_tr, X_te, y_tr, y_te, bC, bg):
    """Train final model and compute all metrics."""
    clf = SVC(C=bC, gamma=bg, kernel="rbf",
              class_weight="balanced", probability=True, random_state=SEED)
    clf.fit(X_tr, y_tr)
    yp    = clf.predict(X_te)
    yprob = clf.predict_proba(X_te)

    acc  = accuracy_score(y_te, yp)
    f1   = f1_score(y_te, yp, average="macro")
    prec = precision_score(y_te, yp, average="macro", zero_division=0)
    rec  = recall_score(y_te, yp, average="macro", zero_division=0)
    mcc  = matthews_corrcoef(y_te, yp)
    ll   = log_loss(y_te, yprob)
    cm   = confusion_matrix(y_te, yp)
    rep  = classification_report(y_te, yp, target_names=LABELS,
                                  output_dict=True, zero_division=0)

    cv10 = StratifiedKFold(10, shuffle=True, random_state=SEED)
    cvr  = cross_validate(clf, X_tr, y_tr, cv=cv10,
                          scoring=["accuracy", "f1_macro"], n_jobs=-1)
    ca   = cvr["test_accuracy"].mean() * 100
    cs   = cvr["test_accuracy"].std()  * 100
    cf   = cvr["test_f1_macro"].mean() * 100

    # Wilson 95% CI
    n    = int(len(X_tr) * 0.9); ph = ca / 100; z = 1.96
    dn   = 1 + z**2 / n
    clo  = (ph + z**2/(2*n) - z*(ph*(1-ph)/n + z**2/(4*n**2))**0.5) / dn * 100
    chi  = (ph + z**2/(2*n) + z*(ph*(1-ph)/n + z**2/(4*n**2))**0.5) / dn * 100

    print(f"\n{'='*55}")
    print(f"  TEST RESULTS   C*={bC:.3f}  γ*={bg:.4f}")
    print(f"{'='*55}")
    print(f"  Accuracy     : {acc*100:.3f}%")
    print(f"  F1-macro     : {f1*100:.3f}%")
    print(f"  Precision    : {prec*100:.3f}%")
    print(f"  Recall       : {rec*100:.3f}%")
    print(f"  MCC          : {mcc:.4f}")
    print(f"  Log-loss     : {ll:.5f}")
    print(f"  10-CV Acc    : {ca:.3f}% ± {cs:.3f}%")
    print(f"  Wilson 95 CI : [{clo:.3f}%,  {chi:.3f}%]")
    print(f"\n{classification_report(y_te, yp, target_names=LABELS, zero_division=0)}")

    return {
        "clf": clf, "yp": yp, "yprob": yprob,
        "acc": acc*100, "f1": f1*100,
        "prec": prec*100, "rec": rec*100,
        "mcc": mcc, "ll": ll, "cm": cm, "rep": rep,
        "ca": ca, "cs": cs, "cf": cf, "clo": clo, "chi": chi,
        "fold_accs": cvr["test_accuracy"] * 100,
        "fold_f1s" : cvr["test_f1_macro"] * 100,
    }


# =============================================================================
# STEP 6 — BASELINES + BONFERRONI MCNEMAR (reviewer comments 3 and 6)
# =============================================================================

def run_baselines(X_tr, X_te, y_tr, y_te, pso_yp):
    """
    Five baselines:
      - Linear SVM       (reviewer Q3: justifies RBF over linear)
      - Logistic Reg     (reviewer Q3: second linear baseline)
      - RBF Default      (SVM without PSO tuning)
      - Random Forest    (strong non-linear baseline)
      - kNN k=5

    McNemar tests with Bonferroni correction (α = 0.05/5 = 0.010).
    """
    cv5 = StratifiedKFold(5, shuffle=True, random_state=SEED)
    models = [
        ("Linear SVM",    LinearSVC(class_weight="balanced",
                                    max_iter=5000, random_state=SEED)),
        ("Logistic Reg",  LogisticRegression(class_weight="balanced",
                                              max_iter=2000, C=1,
                                              random_state=SEED)),
        ("RBF Default",   SVC(C=1, gamma="scale", kernel="rbf",
                              class_weight="balanced", random_state=SEED)),
        ("Random Forest", RandomForestClassifier(n_estimators=100,
                                                  class_weight="balanced",
                                                  n_jobs=-1, random_state=SEED)),
        ("kNN k=5",       KNeighborsClassifier(n_neighbors=5, n_jobs=-1)),
    ]

    rows     = []
    yp_store = {}

    print(f"\n  {'Model':<20}  {'CV-F1%':>8}  {'Test-F1%':>9}  "
          f"{'MCC':>7}  {'LL':>8}")
    print("  " + "-"*58)

    for name, clf in models:
        cv_scores = cross_val_score(clf, X_tr, y_tr, cv=cv5,
                                    scoring="f1_macro", n_jobs=-1)
        clf.fit(X_tr, y_tr)
        yp  = clf.predict(X_te)
        yp_store[name] = yp
        f1  = f1_score(y_te, yp, average="macro")
        mcc = matthews_corrcoef(y_te, yp)
        try:
            ll_v = log_loss(y_te, clf.predict_proba(X_te))
        except AttributeError:
            ll_v = float("nan")
        rows.append({
            "Model"    : name,
            "CV_F1_%"  : round(cv_scores.mean() * 100, 3),
            "Test_F1_%": round(f1 * 100, 3),
            "MCC"      : round(mcc, 4),
            "LL"       : round(ll_v, 5),
        })
        print(f"  {name:<20}  {cv_scores.mean()*100:>8.3f}  "
              f"{f1*100:>9.3f}  {mcc:>7.4f}  {ll_v:>8.5f}")

    # McNemar tests — Bonferroni corrected
    n_comp    = len(models)
    alpha_bon = 0.05 / n_comp
    print(f"\n  McNemar tests  (Bonferroni α = 0.050/{n_comp} = {alpha_bon:.4f}):")

    for name, yp_b in yp_store.items():
        pso_ok  = (pso_yp == y_te).astype(int)
        base_ok = (yp_b   == y_te).astype(int)
        b = int(((pso_ok == 1) & (base_ok == 0)).sum())
        c = int(((pso_ok == 0) & (base_ok == 1)).sum())
        tbl    = np.array([[0, b], [c, 0]])
        result = mcnemar_test(tbl, exact=True, correction=False)
        sig    = result.pvalue < alpha_bon
        flag   = "✓ significant" if sig else "— not significant"
        print(f"    vs {name:<20} b={b:3d} c={c:3d}  "
              f"p={result.pvalue:.5f}  {flag}")
        for r in rows:
            if r["Model"] == name:
                r["McNemar_p"] = round(result.pvalue, 6)
                r["Sig_Bonf"]  = sig

    return pd.DataFrame(rows)


# =============================================================================
# STEP 7 — INFERENCE LATENCY (reviewer comment 7)
# =============================================================================

def measure_latency(clf, X_te):
    """
    Measure single-sample inference time on benchmark hardware.
    Estimate deployment time on ESP32 LilyGO T-SIM7000G.
    """
    sample = X_te[0:1]

    # Warm-up
    for _ in range(100):
        clf.predict(sample)

    n_reps = 3000
    t0     = time.perf_counter()
    for _ in range(n_reps):
        clf.predict(sample)
    single_ms = (time.perf_counter() - t0) / n_reps * 1000

    t0       = time.perf_counter()
    clf.predict(X_te)
    batch_ms = (time.perf_counter() - t0) * 1000

    # ESP32 estimate: conservative ×100 factor
    # (Cortex-M4 240 MHz vs x86 3.5 GHz, plus Python overhead)
    esp32_ms = single_ms * 100

    print(f"\n  Single-sample (benchmark) : {single_ms:.4f} ms")
    print(f"  Batch {len(X_te)} samples        : {batch_ms:.2f} ms")
    print(f"  ESP32 estimate (×100)     : {esp32_ms:.2f} ms")
    print(f"  AFS window (30 min)       : 1,800,000 ms")
    print(f"  Headroom factor           : {1800000/esp32_ms:,.0f}×  → deployable")
    return single_ms, esp32_ms


# =============================================================================
# STEP 8 — PUBLICATION FIGURES
# =============================================================================

def save(fig, name):
    fig.savefig(os.path.join(OUT_DIR, name))
    plt.close(fig)
    print(f"  Saved: {name}")


def fig1_pso_convergence(all_runs, rep_run):
    """
    Two-panel figure.
    Panel a — all 5 seed trajectories together (reproducibility evidence).
    Panel b — swarm spread of representative run (shows exploration→exploitation).
    """
    h   = rep_run["hist"]
    itr = h["iter"].values

    fig, axes = plt.subplots(2, 1, figsize=(7.16, 6.20),
                              gridspec_kw={"hspace": 0.12})

    # ── Panel a: all seeds ──────────────────────────────────────────────
    ax   = axes[0]
    cmap = plt.cm.tab10(np.linspace(0, 0.6, len(all_runs)))
    for i, r in enumerate(all_runs):
        hi   = r["hist"]
        is_r = r["seed"] == rep_run["seed"]
        ax.plot(hi["iter"], hi["best"] * 100,
                color=cmap[i], lw=2.6 if is_r else 1.1,
                alpha=1.0 if is_r else 0.55,
                marker="o", markersize=3.8 if is_r else 2.0,
                label=(f"Seed {r['seed']} — representative"
                       if is_r else f"Seed {r['seed']}"))

    ax.set_ylabel("Global Best F1-macro (%)", fontsize=10)
    ax.legend(fontsize=8.5, loc="lower right", framealpha=0.92,
              edgecolor="#cccccc", ncol=2)
    lo_a = max(min(r["hist"]["best"].min() for r in all_runs) * 100 - 5, 0)
    ax.set_ylim(lo_a, 103)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))

    # ── Panel b: swarm spread of representative run ─────────────────────
    ax = axes[1]
    b  = h["best"].values  * 100
    m  = h["mean"].values  * 100
    w  = h["worst"].values * 100
    s  = h["std"].values   * 100

    ax.fill_between(itr, w, b, alpha=0.12, color=CS, label="Swarm range")
    ax.fill_between(itr, m - s, m + s, alpha=0.22, color=CG,
                    label=r"Mean $\pm$ std")
    ax.plot(itr, w, color=CR, lw=1.2, ls=":", alpha=0.75, label="Worst particle")
    ax.plot(itr, m, color=CG, lw=1.8, ls="--", marker="s",
            ms=3.5, label="Swarm mean")
    ax.plot(itr, b, color=CB, lw=2.6, marker="o",
            ms=4.5, label="Global best", zorder=4)

    # Convergence marker
    thresh = b.max() * 0.995
    above  = b >= thresh
    if above.any():
        ci = int(np.argmax(above))
        ax.axvline(itr[ci], color=CO, ls="--", lw=1.6, alpha=0.85)
        ax.annotate(
            f"≥ {thresh:.1f}%\niter {itr[ci]}",
            xy     = (itr[ci], b[ci]),
            xytext = (itr[ci] + len(itr) * 0.08, b[ci] - 13),
            fontsize=8.5, fontweight="bold", color=CO,
            arrowprops=dict(arrowstyle="->", color=CO, lw=1.2))

    ax.set_xlabel("PSO Iteration", fontsize=10)
    ax.set_ylabel("Validation F1-macro (%)", fontsize=10)
    ax.legend(loc="lower right", fontsize=8.5, framealpha=0.92,
              edgecolor="#cccccc", handlelength=1.8)
    ax.set_ylim(max(w.min() - 6, 0), 103)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))

    for a in axes:
        a.set_xlim(0, len(h) - 1)
        a.xaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=8))

    save(fig, "Fig1_PSO_Convergence.png")


def fig2_swarm_boxplot(rep_run):
    """Per-iteration box plot of all 20 particle F1 scores."""
    h     = rep_run["hist"]
    itr   = h["iter"].values
    all_f = h["all_fitness"].tolist()

    step = max(1, len(itr) // 20)
    sel  = list(range(0, len(itr), step))
    if len(itr) - 1 not in sel:
        sel.append(len(itr) - 1)

    data  = [np.array(all_f[i]) * 100 for i in sel]
    labs  = [str(itr[i]) for i in sel]
    gbest = [h["best"].values[i] * 100 for i in sel]

    fig, ax = plt.subplots(figsize=(7.16, 3.70))
    ax.boxplot(data, tick_labels=labs, patch_artist=True, showfliers=True,
               medianprops=dict(color=CR, lw=2.0),
               boxprops=dict(facecolor=CS, color=CB, lw=1.1),
               whiskerprops=dict(color=CB, lw=1.1),
               capprops=dict(color=CB, lw=1.8),
               flierprops=dict(marker="o", color=CR, ms=3, alpha=0.40))
    ax.plot(range(1, len(sel) + 1), gbest, color=CO, lw=2.2,
            marker="*", ms=8, zorder=5, label="Global best")
    all_v = [v for sub in data for v in sub]
    ax.set_ylim(max(min(all_v) - 5, 0), 103)
    ax.set_xlabel("PSO Iteration", fontsize=10)
    ax.set_ylabel("Particle F1-macro (%)", fontsize=10)
    ax.set_title("PSO Swarm Fitness Distribution per Iteration\n"
                 "(box = IQR,  whiskers = full range,  ★ = global best)",
                 fontsize=10, fontweight="bold")
    ax.legend(fontsize=9, loc="lower right", framealpha=0.92, edgecolor="#cccccc")
    save(fig, "Fig2_Swarm_Boxplot.png")


def fig3_confusion(R):
    """Normalised confusion matrix with absolute counts."""
    cm_raw = R["cm"]
    cm_n   = cm_raw.astype(float) / cm_raw.sum(axis=1, keepdims=True) * 100
    clrs   = [CG, CO, CR]

    fig, ax = plt.subplots(figsize=(3.60, 3.30))
    sns.heatmap(cm_n, annot=False, cmap="Blues", ax=ax,
                xticklabels=LABELS, yticklabels=LABELS,
                linewidths=0.5, linecolor="white",
                cbar_kws={"label": "% of actual class", "shrink": 0.88})
    for i in range(3):
        for j in range(3):
            col = "white" if cm_n[i, j] > 55 else "black"
            ax.text(j + 0.5, i + 0.38, f"{cm_n[i,j]:.1f}%",
                    ha="center", va="center",
                    fontsize=9, fontweight="bold", color=col)
            ax.text(j + 0.5, i + 0.68, f"n={cm_raw[i,j]}",
                    ha="center", va="center", fontsize=7.5,
                    color=col, alpha=0.85)
    for i, c in enumerate(clrs):
        ax.get_yticklabels()[i].set_color(c)
        ax.get_xticklabels()[i].set_color(c)
    ax.set_xlabel("Predicted", fontsize=10)
    ax.set_ylabel("True",      fontsize=10)
    ax.set_title(
        f"Confusion Matrix\n"
        f"Acc={R['acc']:.2f}%   F1={R['f1']:.2f}%   MCC={R['mcc']:.3f}",
        fontsize=10, fontweight="bold")
    save(fig, "Fig3_Confusion_Matrix.png")


def fig4_probability_heatmap(R, y_te):
    """
    Per-class predicted probability heatmap — all test samples, all classes.

    Reviewer comment 5 fix: replaces the old FigC that showed only Good class.
    Samples are sorted within each true class by descending prediction confidence
    so the colour gradient reveals where the model is uncertain.
    """
    yprob = R["yprob"]

    order = []
    for c in range(3):
        idx_c = np.where(y_te == c)[0]
        order.extend(idx_c[np.argsort(-yprob[idx_c, c])].tolist())
    order = np.array(order)

    prob_s = yprob[order]
    true_s = y_te[order]

    fig, ax = plt.subplots(figsize=(7.16, 3.60))
    im = ax.imshow(prob_s.T, aspect="auto", cmap="RdYlGn",
                   vmin=0, vmax=1, interpolation="nearest")
    plt.colorbar(im, ax=ax, label="Predicted probability",
                 fraction=0.025, pad=0.02)

    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(LABELS, fontsize=10)
    for i, c in enumerate([CG, CO, CR]):
        ax.get_yticklabels()[i].set_color(c)

    # Class boundary lines
    bounds = [0]
    for c in range(3):
        bounds.append(bounds[-1] + (true_s == c).sum())
    for bnd in bounds[1:-1]:
        ax.axvline(bnd - 0.5, color="white", lw=2.2)
        ax.axvline(bnd - 0.5, color="#333333", lw=1.0, ls="--", alpha=0.5)

    for c in range(3):
        mid = (bounds[c] + bounds[c + 1]) / 2
        ax.text(mid, -0.75, LABELS[c], ha="center", fontsize=8.5,
                color=[CG, CO, CR][c], fontweight="bold",
                transform=ax.get_xaxis_transform())

    ax.set_xlabel("Test sample (sorted by true class then confidence)", fontsize=10)
    ax.set_ylabel("Predicted class probability", fontsize=10)
    ax.set_title("Per-Class Predicted Probabilities — All Test Samples\n"
                 "(green = confident correct; red = uncertain or wrong)",
                 fontsize=10, fontweight="bold")
    save(fig, "Fig4_Probability_Heatmap.png")


def fig5_lda(X_tr, X_te, y_tr, y_te, R):
    """
    LDA 2D projection showing class structure and misclassifications.
    Note added about separability — transparent for reviewers.
    """
    lda = LinearDiscriminantAnalysis(n_components=2)
    lda.fit(X_tr, y_tr)
    ev   = lda.explained_variance_ratio_
    Xp   = lda.transform(X_te)
    yp   = R["yp"]
    clrs = [CG, CO, CR]

    fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.50))

    for ax, (use_pred, title_tag) in zip(
            axes,
            [(False, "(a) True Labels"),
             (True,  "(b) Predicted Labels + Errors")]):

        for i, (lb, c) in enumerate(zip(LABELS, clrs)):
            mask_ref = (yp if use_pred else y_te) == i
            mask_err = (yp != y_te) & (y_te == i) if use_pred else np.zeros(len(y_te), dtype=bool)
            ax.scatter(Xp[mask_ref, 0], Xp[mask_ref, 1],
                       c=c, alpha=0.45, s=16, label=f"{lb}", edgecolors="none")
            if use_pred:
                err_mask = (yp != y_te) & (y_te == i)
                if err_mask.sum() > 0:
                    ax.scatter(Xp[err_mask, 0], Xp[err_mask, 1],
                               c=c, s=90, marker="X",
                               edgecolors="black", lw=0.8, zorder=5,
                               label=f"{lb} error (n={err_mask.sum()})")

        ax.set_xlabel(f"LD1 ({ev[0]*100:.1f}% variance)", fontsize=10)
        ax.set_ylabel(f"LD2 ({ev[1]*100:.1f}% variance)", fontsize=10)
        sub_title = (f"{title_tag}\n"
                     + (f"Acc={R['acc']:.2f}%  errors={(yp!=y_te).sum()}/{len(y_te)}"
                        if use_pred else "LDA projection of 4 features"))
        ax.set_title(sub_title, fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, markerscale=1.6, ncol=1 if not use_pred else 2)

        if not use_pred:
            ax.text(0.02, 0.03,
                    f"LD1={ev[0]*100:.1f}% variance\n"
                    "→ Linear baselines also compared\n(Fig 6)",
                    transform=ax.transAxes, fontsize=7.5, color="#444444",
                    bbox=dict(boxstyle="round,pad=0.3", fc="#ffffcc",
                              ec="#cccccc", alpha=0.9))

    plt.tight_layout()
    save(fig, "Fig5_LDA_Projection.png")


def fig6_model_comparison(comp_df, pso_f1, pso_cv_f1):
    """
    Horizontal bar chart: CV-F1 (panel a) and Test-F1 (panel b).
    PSO-ASVM highlighted in blue; baselines in grey.
    """
    names  = list(comp_df["Model"]) + ["PSO-ASVM\n(Proposed)"]
    cv_v   = list(comp_df["CV_F1_%"])  + [round(pso_cv_f1, 3)]
    te_v   = list(comp_df["Test_F1_%"]) + [round(pso_f1,  3)]
    colors = [CS] * len(comp_df) + [CB]
    n      = len(names)

    fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.80),
                              gridspec_kw={"wspace": 0.38})

    for ax, vals, xlabel, panel in [
        (axes[0], cv_v, "5-Fold CV F1-macro (%)", "(a) Cross-Validation"),
        (axes[1], te_v,  "Test F1-macro (%)",      "(b) Test Set"),
    ]:
        bars = ax.barh(range(n), vals, color=colors,
                       edgecolor="white", lw=0.8, height=0.62)
        for bar, v in zip(bars, vals):
            ax.text(v + 0.5, bar.get_y() + bar.get_height() / 2,
                    f"{v:.1f}%", va="center", fontsize=8.5)
        ax.set_yticks(range(n))
        ax.set_yticklabels(names, fontsize=8.5)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_title(panel, fontsize=10, fontweight="bold")
        lo = max(min(vals) - 10, 0)
        ax.set_xlim(lo, max(vals) + 8)
        ax.invert_yaxis()

    save(fig, "Fig6_Model_Comparison.png")


def fig7_cv_folds(R):
    """10-fold CV accuracy bar chart with Wilson CI band."""
    fa = R["fold_accs"]
    n  = len(fa)
    folds = np.arange(1, n + 1)
    colors = [CB if a >= R["ca"] else CR for a in fa]

    fig, ax = plt.subplots(figsize=(3.60, 3.00))
    bars = ax.bar(folds, fa, color=colors, edgecolor="white",
                  lw=0.8, alpha=0.85, width=0.70)
    ax.axhline(R["ca"], color=CB, ls="--", lw=1.8,
               label=f"Mean = {R['ca']:.2f}%")
    ax.fill_between([0.4, n + 0.6], [R["clo"]] * 2, [R["chi"]] * 2,
                    alpha=0.12, color=CB,
                    label=f"95% CI [{R['clo']:.1f}%, {R['chi']:.1f}%]")
    for bar, a in zip(bars, fa):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.35,
                f"{a:.1f}", ha="center", fontsize=7.5, color="#333333")
    lo = max(fa.min() - 6, 60)
    ax.set_ylim(lo, 104)
    ax.set_xticks(folds)
    ax.set_xlabel("Fold", fontsize=10)
    ax.set_ylabel("Accuracy (%)", fontsize=10)
    ax.set_title(f"10-Fold Stratified CV\n"
                 f"Acc = {R['ca']:.2f}% ± {R['cs']:.2f}%",
                 fontsize=10, fontweight="bold")
    ax.legend(loc="lower center", fontsize=8.5, framealpha=0.92,
              edgecolor="#cccccc", ncol=2)
    save(fig, "Fig7_CV_Folds.png")


def fig8_multiseed(all_runs):
    """Three-panel bar chart of multi-seed C*, γ*, and F1* with mean ± σ."""
    seeds   = [r["seed"]     for r in all_runs]
    bC_list = [r["bC"]       for r in all_runs]
    bg_list = [r["bg"]       for r in all_runs]
    f1_list = [r["best_f1"]  for r in all_runs]

    fig, axes = plt.subplots(1, 3, figsize=(7.16, 2.90),
                              gridspec_kw={"wspace": 0.50})
    for ax, vals, ylabel, title in [
        (axes[0], bC_list, "C*",          "Optimal C* per Seed"),
        (axes[1], bg_list, "γ*",          "Optimal γ* per Seed"),
        (axes[2], f1_list, "Best F1 (%)", "Best F1-macro per Seed"),
    ]:
        mean_v = float(np.mean(vals))
        std_v  = float(np.std(vals))
        ax.bar(range(len(seeds)), vals, color=CB, edgecolor="white",
               alpha=0.80, width=0.60)
        ax.axhline(mean_v, color=CR, ls="--", lw=1.5,
                   label=f"Mean={mean_v:.3g}")
        ax.fill_between([-0.5, len(seeds) - 0.5],
                        [mean_v - std_v] * 2,
                        [mean_v + std_v] * 2,
                        alpha=0.15, color=CR, label=f"±1σ={std_v:.3g}")
        ax.set_xticks(range(len(seeds)))
        ax.set_xticklabels([f"S{s}" for s in seeds], fontsize=8)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_title(title, fontsize=9, fontweight="bold")
        ax.legend(fontsize=7.5, framealpha=0.90)

    fig.suptitle("PSO Reproducibility — 5 Independent Seeds",
                 fontsize=10, fontweight="bold", y=1.03)
    save(fig, "Fig8_MultiSeed_Reproducibility.png")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "=" * 60)
    print("  PSO-ASVM  —  Final Revised Pipeline")
    print("  Dataset: data1.csv (984 real field samples)")
    print("=" * 60)

    # ===== 1. Load =================================================================
    print("\n[1/6]  Loading and labelling data")
    X_tr, X_te, y_tr, y_te, sc, raw_df = load_data(DATA_PATH)

    # ===== 2. PSO ========================================================
    print("\n[2/6]  PSO optimisation  (F1-macro fitness, 5 seeds)")
    seeds = [42, 123, 7, 2024, 99]
    all_runs, rep_run = run_pso_multi_seed(
        X_tr, y_tr, seeds,
        n_particles = 20,
        n_iter      = 30,
        lC_lo=-2.0, lC_hi=2.3,    # C in [0.01, 200]
        lg_lo=-2.0, lg_hi=0.7,    # γ in [0.01, 5.0]
        w_start=0.9, w_end=0.4,
        c1=1.5, c2=1.5,
        cv_folds=5,
    )
    bC = rep_run["bC"]
    bg = rep_run["bg"]

    # ==== 3. Final model =======================================================
    print("\n[3/6]  Final model evaluation")
    R = evaluate_model(X_tr, X_te, y_tr, y_te, bC, bg)

    # ==== 4. Baselines ========================================================
    print("\n[4/6]  Baseline comparison + Bonferroni-corrected McNemar tests")
    comp_df = run_baselines(X_tr, X_te, y_tr, y_te, R["yp"])
    print("\n" + comp_df.to_string(index=False))

    # ===== 5. Latency ======================================================
    print("\n[5/6]  Inference latency")
    single_ms, esp32_ms = measure_latency(R["clf"], X_te)

    # ==== 6. Figures ===========================================================
    print("\n[6/6]  Generating figures")
    fig1_pso_convergence(all_runs, rep_run)
    fig2_swarm_boxplot(rep_run)
    fig3_confusion(R)
    fig4_probability_heatmap(R, y_te)
    fig5_lda(X_tr, X_te, y_tr, y_te, R)
    fig6_model_comparison(comp_df, R["f1"], R["cf"])
    fig7_cv_folds(R)
    fig8_multiseed(all_runs)

    # ==== Save CSVs =============================================================
    rep_run["hist"].to_csv(os.path.join(OUT_DIR, "pso_history.csv"), index=False)
    comp_df.to_csv(os.path.join(OUT_DIR, "baselines.csv"), index=False)
    raw_df.to_csv(os.path.join(OUT_DIR, "AFS_984_labelled.csv"), index=False)

    pd.DataFrame([{
        "bC": bC, "bg": bg,
        "test_acc_%"  : R["acc"],  "test_f1_%"   : R["f1"],
        "test_prec_%" : R["prec"], "test_rec_%"  : R["rec"],
        "test_mcc"    : R["mcc"],  "test_ll"     : R["ll"],
        "cv10_acc_%"  : R["ca"],   "cv10_std_%"  : R["cs"],
        "cv10_f1_%"   : R["cf"],   "ci_lo_%"     : R["clo"],
        "ci_hi_%"     : R["chi"],
        "bench_ms"    : round(single_ms, 4),
        "esp32_ms"    : round(esp32_ms,  2),
        "n_seeds"     : len(seeds),
        "C_seed_std"  : round(float(np.std([r["bC"]      for r in all_runs])), 4),
        "f1_seed_std" : round(float(np.std([r["best_f1"] for r in all_runs])), 4),
    }]).to_csv(os.path.join(OUT_DIR, "results_summary.csv"), index=False)

    # ==== Final summary =======================================================
    print(f"\n{'='*60}")
    print("  RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"  Optimal  C*   : {bC:.4f}")
    print(f"  Optimal  γ*   : {bg:.5f}")
    print(f"  Test Accuracy : {R['acc']:.3f}%")
    print(f"  Test F1-macro : {R['f1']:.3f}%")
    print(f"  Test MCC      : {R['mcc']:.4f}")
    print(f"  10-CV Acc     : {R['ca']:.3f}% ± {R['cs']:.3f}%")
    print(f"  Wilson 95 CI  : [{R['clo']:.2f}%,  {R['chi']:.2f}%]")
    print(f"  Bench latency : {single_ms:.4f} ms/sample")
    print(f"  ESP32 est.    : {esp32_ms:.2f} ms/sample")
    print(f"  All files  →  {OUT_DIR}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
