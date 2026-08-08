
Important: 

Here are files for latency measurement using LilyGo board, Arduino IDE, and python program



a) model.h: the trained model, ALREADY VERIFIED here to reproduce scikit-learn (0/197).
   TDS is in mg/L (matches the manuscript). 154 support vectors, 4 features, 3 classes.
b) svm_benchmark.ino 
c) build_model_h.py
d) AFS_Huaian_Dataset.csv : the 984-sample dataset.

To measure latency:

1- Move model.h and svm_benchmark.ino in the SAME folder. Open the .ino in Arduino IDE.
2- Tools > Board: LilyGo. Select the correct COM port.
3- Upload. Open Serial Monitor at 115200 baud. Press the board's EN/RESET button once.
4- Read the output IN THIS ORDER:
   a. "Verification: 0 / 197 disagreements"      <-- MUST be 0.
   b. "esp_timer : ... mean X.XXX ms / inference" <-- THIS is the latency measurement
   c. "cycle-ctr : mean ... sd ..."               <-- the standard deviation
   d. "Headroom  : ... x"                          <-- recomputed automatically.


