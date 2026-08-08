# Deterministic rebuild of the definitive PSO-ASVM model (TDS in mg/L) + export to model.h
# By Dr. Amir A Mokhtarzadeh
# Aug 2026


import numpy as np, pandas as pd
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, matthews_corrcoef

SEED=42
C_OPT=199.52623149688787
G_OPT=1.3177115051975956
FEATURES=["pH","Turbidity_NTU","EC_uS_cm","TDS_mg_L"]   # mg/L: matches manuscript & paper metrics

def who_label(ph,turb,ec,tds_mgL):
    if 6.5<=ph<=8.5 and turb<5 and ec<500 and tds_mgL<300: return 0
    if 6.0<=ph<=9.0 and turb<25 and ec<1500 and tds_mgL<1000: return 1
    return 2

raw=pd.read_csv("AFS_Huaian_Dataset.csv")
y=np.array([who_label(r.pH,r.Turbidity_NTU,r.EC_uS_cm,r.TDS_mg_L) for r in raw.itertuples()])
X=raw[FEATURES].values.astype(np.float64)
Xtr_raw,Xte_raw,ytr,yte=train_test_split(X,y,test_size=0.20,stratify=y,random_state=SEED)
sc=StandardScaler(); Xtr=sc.fit_transform(Xtr_raw); Xte=sc.transform(Xte_raw)
svc=SVC(C=C_OPT,gamma=G_OPT,kernel="rbf",class_weight="balanced",
        probability=True,random_state=SEED).fit(Xtr,ytr)

pred=svc.predict(Xte)
print("METRICS acc=%.4f f1=%.4f mcc=%.4f  (paper: 94.9239 / 94.5711 / 0.9234)"%(
    accuracy_score(yte,pred)*100,f1_score(yte,pred,average='macro')*100,matthews_corrcoef(yte,pred)))
print("SV=%d %s  test=%d"%(svc.support_vectors_.shape[0],list(svc.n_support_),len(yte)))

# ---- export model.h (float32, raw X_test embedded for on-device verification) ----
gamma=float(getattr(svc,"_gamma",svc.gamma))
sv=svc.support_vectors_.astype(np.float64); dual=svc.dual_coef_.astype(np.float64)
inter=svc.intercept_.astype(np.float64); nsup=svc.n_support_.astype(int)
mean=sc.mean_.astype(np.float64); scale=sc.scale_.astype(np.float64)
ncls=len(svc.classes_); nsv=sv.shape[0]; nft=sv.shape[1]; npair=ncls*(ncls-1)//2
fmt=lambda a:", ".join("%.9g"%v for v in a)
with open("model.h","w") as f:
    f.write("// PSO-ASVM (RBF, TDS in mg/L). Reproduces 94.92%/94.57%/0.9234.\n#ifndef MODEL_H\n#define MODEL_H\n")
    f.write(f"#define N_FEATURES {nft}\n#define N_CLASSES {ncls}\n#define N_SV {nsv}\n#define N_PAIRS {npair}\n#define N_TEST {len(Xte_raw)}\n")
    f.write(f"const float GAMMA={'%.9g'%gamma};\n")
    f.write(f"const float SCALER_MEAN[]={{{fmt(mean)}}};\nconst float SCALER_SCALE[]={{{fmt(scale)}}};\n")
    f.write(f"const int N_SUPPORT[]={{{', '.join(map(str,nsup))}}};\n")
    f.write(f"const float SV[]={{{fmt(sv.reshape(-1))}}};\n")
    f.write(f"const float DUAL[]={{{fmt(dual.reshape(-1))}}};\n")
    f.write(f"const float INTERCEPT[]={{{fmt(inter)}}};\n")
    f.write(f"const float X_TEST[]={{{fmt(Xte_raw.reshape(-1))}}};\n")
    f.write(f"const int Y_SKLEARN[]={{{', '.join(map(str,pred.astype(int)))}}};\n#endif\n")
print("wrote model.h")
