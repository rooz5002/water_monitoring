# AFS — Autonomous Floating Station for Real-Time Water Quality Monitoring

An IoT-enabled, solar-powered floating station that measures pH, turbidity, electrical conductivity (EC), and total dissolved solids (TDS) in surface water, classifies each reading into a WHO-based water-quality class (**Good / Moderate / Poor**) on-device, and reports results over 4G LTE-GPRS with GPS geotagging — no laboratory sampling, no cloud dependency for inference.

Classification is done by **PSO-ASVM**: a Particle-Swarm-Optimised Adaptive Support Vector Machine, tuned and validated on 984 real field measurements from six ecologically distinct sites, then exported and benchmarked directly on the target embedded MCU (ESP32 LilyGO T-SIM7000G).

> This repository accompanies the manuscript *"An IoT Enabled Autonomous Floating Station for Real-Time Water Quality Monitoring with Machine Learning"* (currently in final peer review). Citation details will be added once the paper is accepted/published.

---

## Highlights

- **94.924% test accuracy**, 94.571% macro-F1, MCC = 0.9234 on a held-out 197-sample test set — statistically equivalent to Random Forest (McNemar p = 0.146) and significantly better than Linear SVM, Logistic Regression, RBF-default SVM, and kNN (all p < 0.00001, Bonferroni-corrected).
- **Zero Good-class false positives for genuinely Poor water** — the model never labelled contaminated water as safe.
- **0.2494 ms/inference measured on-device** on the ESP32 LilyGO T-SIM7000G (esp_timer, averaged over 2000 inferences), a ~7.2-million-times headroom against the 30-minute sample cycle.
- **Self-cleaning sealed sampling chamber** with an active drain-and-dry cycle after every measurement, avoiding the 7–14 day biofouling limit of continuous-immersion sensors.
- **Solar-powered, autonomous 2–3 years** of unattended operation (10 W panel + Li-ion cell), with a **measured** daily requirement of 2.76 Wh (21 mWh per 160 s active cycle, 20 mA deep sleep) — 2.6 days of battery-only autonomy on a 2400 mAh cell (4.3 days on 4000 mAh); see [`Power_measurement/`](Power_measurement/).
- **Passive LDR-pair differential repositioning** — the station keeps station on open water using only a photoresistor pair and an H-bridge motor driver, without GPS-based station-keeping.
- Field-validated at **six sites** (three open freshwater lakes, three urban waterways) in Huai'an, Jiangsu, China.

---

## Repository contents

```
water_monitoring/
├── new-file-pso-asvm.py       # Main PSO-ASVM pipeline: training, PSO tuning, validation,
│                               #   figure/table generation (Figs 1–15, Tables in pso_results/)
├── AFS_Huaian_Dataset.csv     # 984 real field samples: pH, Turbidity_NTU, EC_uS_cm, TDS_ppm,
│                               #   TDS_mg_L, WQI_Class/Code, site/GPS metadata, battery state
├── pso_results/                 # Generated outputs from new-file-pso-asvm.py, figures renamed
│   │                            #   to match the manuscript's Figure 5–14 numbering
│   ├── pso_history.csv          #   per-iteration PSO swarm fitness trace
│   ├── results_summary.csv      #   final PSO-optimal (C, γ) and headline metrics
│   ├── baselines.csv            #   comparison vs Linear SVM / LR / RBF-default / RF / kNN
│   ├── Fig5_PSO_Convergence.png
│   ├── Fig6_Swarm_Boxplot.png
│   ├── Fig7_MultiSeed_Reproducibility.png
│   ├── Fig8_Confusion_Matrix.png
│   ├── Fig9_Probability_Heatmap.png
│   ├── Fig11_CV_Folds.png
│   ├── Fig13_LDA_Projection.png
│   ├── Fig14_Model_Comparison.png
│   └── legacy_figures/          #   Fig10 (ROC/PR), Fig12 (learning curve), and alternate
│                                #   renders of Fig11/Fig14 from an earlier, no-longer-present
│                                #   version of the script — see legacy_figures/README.md.
│                                #   Manuscript Fig. 15 (feature importance) is not reproducible
│                                #   from the code in this repo.
├── Latency_test/               # On-device (ESP32) inference latency benchmark
│   ├── build_model_h.py        #   deterministic rebuild of the PSO-optimal SVM (C=199.526, γ=1.318)
│   ├── export_model.py         #   exports the fitted scaler + SVC to a C header (model.h)
│   ├── model.h                 #   exported model: 154 support vectors, 4 features, 3 classes
│   ├── svm_benchmark.ino       #   Arduino sketch: on-device inference + esp_timer latency measurement
│   └── readme_first.txt        #   step-by-step latency measurement instructions
└── Power_measurement/          # Measured power budget on the T-SIM7000G
    ├── afs_power_test/         #   Arduino sketch re-creating the 30-min cycle with phase markers
    ├── afs_measure.py          #   guided laptop-side measurement + report generator
    ├── results/                #   power_report*.md, measurements.json, raw serial logs
    └── README.md               #   results, method, limitations, wiring
```

---

## Hardware architecture

The AFS is built around an **ESP32 WROVER-B** (dual-core, integrated SIM7000G LTE/GPRS/GPS modem) inside a sealed, weatherproofed polystyrene floating hull, with:

- A sealed water-sampling chamber housing the pH, turbidity, EC, and TDS sensors
- A water pump and air blower for the drain/dry self-cleaning cycle
- A dual H-bridge motor driver + LDR pair for passive differential repositioning
- A 10 W solar panel, charge-control IC, and Li-ion battery backup
- Micro-SD buffering for offline resilience when GPRS/Wi-Fi transmission fails

**Six-phase operational cycle** (every 30 minutes): Idle/sleep → Water sampling → Sensing → Draining → Drying → Data transmission (JSON payload with sample ID, UTC timestamp, GPS position, sensor readings, battery voltage, and the predicted WHO class, sent via GPRS/Wi-Fi with SD-card fallback).

**Three-layer system architecture**: Core Layer (ESP32, sensors, LDR pair, motor actuators, SD storage) → Communication Layer (LTE-GPRS/Wi-Fi JSON packets, Bluetooth, GPS) → Standard Layer (data storage, PSO-ASVM analytics, contamination prediction, SMS/dashboard alerts).

---

## Dataset

`AFS_Huaian_Dataset.csv` — 984 real field acquisitions from six deployment sites (20 days each): three open freshwater lakes and three urban waterways in Huai'an, Jiangsu, China. Each row is one measurement cycle: `pH`, `Turbidity_NTU`, `EC_uS_cm`, `TDS_ppm` (raw sensor units) / `TDS_mg_L` (converted, ×1000), plus GPS coordinates, timestamp, site metadata, battery state, and the derived WHO quality label.

**WHO threshold labelling** (a sample is Good only if *all four* parameters meet the Good band; Moderate if all stay within the Moderate band; Poor if *any* parameter exceeds the Moderate upper bound):

| Parameter  | Unit   | Good        | Moderate     | Poor              |
|------------|--------|-------------|--------------|-------------------|
| pH         | —      | 6.5 – 8.5   | 6.0 – 9.0    | Outside 6.0 – 9.0 |
| Turbidity  | NTU    | < 5         | < 25         | ≥ 25              |
| EC         | µS/cm  | < 500       | < 1,500      | ≥ 1,500           |
| TDS        | mg/L   | < 300       | < 1,000      | ≥ 1,000           |

Class balance is mildly imbalanced: Good 33.2%, Moderate 27.8%, Poor 38.9% (max:min ratio 1.40).

---

## Method: PSO-ASVM

1. **Feature standardisation** — z-score scaling fit only on the training partition (no leakage into test/CV).
2. **Per-class (adaptive) penalty scaling** — instead of a single global `C`, each class k gets `Ck = C × N / (3·Nk)`, which is equivalent to scikit-learn's `class_weight='balanced'`. This keeps the minority *Moderate* class from being sacrificed for majority-class accuracy.
3. **PSO hyperparameter search** — a 20-particle swarm searches `(C, γ)` in log-space over 30 iterations, maximising **5-fold stratified cross-validated macro-F1** (not accuracy) on the training partition only, with linearly decaying inertia and clamped velocities. Converged, reproducible optimum: **C = 199.526, γ = 1.318** (σ_F1 = 0.333 pp across 5 independent seeds).
4. **RBF-kernel SVM** trained on the PSO-optimal hyperparameters, with **Platt scaling** for calibrated, confidence-aware class probabilities (used for alert escalation in the field).
5. **Validation strategy** — three independent levels to prevent data leakage: a fixed 787/197 stratified hold-out split (test set touched exactly once), 5-fold CV *inside* PSO for fitness evaluation only, and a separate post-training 10-fold stratified CV on the training partition for stability assessment.

## Results

**Per-class performance on the 197-sample held-out test set:**

| Class     | Precision (%) | Recall (%) | F1-Score (%) | Support |
|-----------|---------------|------------|---------------|---------|
| Good      | 94.12         | 98.46      | 96.24         | 65      |
| Moderate  | 92.45         | 89.09      | 90.74         | 55      |
| Poor      | 97.37         | 96.10      | 96.73         | 77      |
| **Macro** | **94.65**     | **94.55**  | **94.57**     | 197     |

- **MCC = 0.9234**, macro ROC AUC = 0.9942, macro PR-AUC (AP) = 0.9865
- 10-fold stratified CV accuracy: **95.95% ± 1.58%** (Wilson 95% CI)
- Learning curve converges by n ≈ 320 training samples; final train/validation overfitting gap = 1.41 pp
- All 10 test-set misclassifications (out of 197) fall exactly at the WHO threshold boundary region in LDA projection space (LD1 explains 99.9% of between-class variance) — consistent with irreducible label-boundary (Bayes) error rather than a model deficiency

**Comparison against baseline classifiers** (same train/test partition, same preprocessing):

| Model                  | CV F1-macro (%) | Test F1-macro (%) | vs PSO-ASVM (pp) | McNemar p (Bonferroni α=0.010) |
|-------------------------|-----------------|--------------------|-------------------|----------------------------------|
| Linear SVM              | 79.887          | 74.157             | +20.41            | p < 0.00001 (significant)        |
| Logistic Regression     | 79.864          | 76.434             | +18.14            | p < 0.00001 (significant)        |
| RBF Default (C=1)       | 71.724          | 72.862             | +21.71            | p < 0.00001 (significant)        |
| kNN (k=5)               | 87.235          | 89.927             | +4.64             | p = 0.035 (n.s. after correction)|
| Random Forest (100 trees)| 98.274         | 97.807             | −3.24             | p = 0.146 (n.s.)                 |
| **PSO-ASVM (proposed)** | **95.769**      | **94.571**         | reference         | —                                |

PSO-ASVM is statistically indistinguishable from Random Forest on this test set, while being far cheaper to run on an embedded MCU and providing calibrated probability outputs via Platt scaling.

## Embedded deployment and latency

The trained scaler + SVC (154 support vectors, 4 features, 3 classes) are exported to a plain C header (`Latency_test/model.h`) via `export_model.py` / `build_model_h.py`, then compiled directly into `svm_benchmark.ino` for the **ESP32 LilyGO T-SIM7000G**.

| Platform                              | Latency / inference |
|----------------------------------------|----------------------|
| x86 workstation (desktop reference)    | 0.1058 ms            |
| **ESP32 LilyGO T-SIM7000G (measured)** | **0.2494 ms** (σ = 0.0064 ms, n = 2000) |

Against the AFS's 30-minute (1,800,000 ms) sample cycle, this gives a **headroom factor of ≈ 7.2 million×** — on-device inference imposes no practical real-time constraint. On-device predictions were verified to reproduce the scikit-learn model's predictions exactly (0/197 disagreements) before latency was trusted; see `Latency_test/readme_first.txt` for the measurement procedure.

## Reproducing the results

```bash
pip install numpy pandas scikit-learn statsmodels matplotlib seaborn
python new-file-pso-asvm.py         # trains PSO-ASVM, runs baselines/validation, writes pso_results/
```

To reproduce the on-device latency benchmark, see `Latency_test/readme_first.txt` (requires a LilyGO T-SIM7000G board and the Arduino IDE).

## Data availability

The full 984-sample field dataset (`AFS_Huaian_Dataset.csv`), including raw sensor readings and derived WHO quality labels, is provided in this repository to support independent verification and reuse.

## License

This repository (code, trained model artifacts, and dataset) is released under **[CC BY-NC 4.0](LICENSE)** (Creative Commons Attribution-NonCommercial 4.0 International) — free to use, share, and adapt for non-commercial purposes with attribution. For commercial use, contact the authors.

## Authors

Developed at the Robotics Lab, Huai'an University, by Amir A. Mokhtarzadeh and H. Naeem.

## Competing interests

The authors declare no known competing financial interests or personal relationships that could have appeared to influence this work.
