"""Step 4b: regression analysis -- which physical factors predict a cold road surface?

Inputs : data/citywide_sample.json (4,000 random DC roads, Earth Engine variables)
         data/replay.json          (hospital corridor segments, for applying the model)
Output : data/regression.js        (window.REG = {...} for regression.html)

Targets are MEASURED by satellites, never the engine's own output (that would be circular):
  lst_day_anom_f   Landsat 8/9 winter surface temperature vs DC median (F), 30 m, clear days 2022-25
  snow_persist     Sentinel-2 share of clear scenes showing snow, Jan 7-20 2025 (after the storm)
  lst_night_anom_f MODIS winter NIGHT surface temperature vs DC median (F) -- 1 km pixels, coarse

Honesty safeguards
  - standard errors clustered by 1 km grid cell (nearby roads are not independent)
  - spatial block cross-validation (2 km blocks held out together) next to ordinary random CV
"""
import json
import math
import os

import numpy as np
import statsmodels.api as sm
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import r2_score, roc_auc_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data")

PREDICTORS = [
    ("elev_m", "Elevation", "m"),
    ("canopy_pct", "Tree canopy", "%"),
    ("impervious_pct", "Impervious surface", "%"),
    ("built_h_m", "Building height", "m"),
    ("terrain_shade", "Terrain shadow (winter)", "share of day"),
    ("northness", "North-facing slope", "cos(aspect)"),
    ("grade_pct", "Road grade", "%"),
    ("log_water_m", "Distance to water (log10)", "log10 m"),
    ("bridge", "Bridge", "0/1"),
]
TARGETS = [
    ("lst_day_anom_f", "Landsat winter surface temp (day)", "°F vs DC median", "30 m"),
    ("snow_persist", "Snow left after Jan 2025 storm", "share of clear scenes", "10–20 m"),
    ("lst_night_anom_f", "MODIS winter surface temp (night)", "°F vs DC median", "1 km — coarse"),
]
KM_LAT, KM_LON = 1 / 111.0, 1 / (111.0 * math.cos(math.radians(38.9)))


def rows_to_matrix(rows):
    X, keep = [], []
    for r in rows:
        r = dict(r)
        r["terrain_shade"] = None if r.get("sun_lit_frac") is None else 1 - r["sun_lit_frac"]
        r["log_water_m"] = math.log10(max(r.get("water_dist_m") or 1, 1))
        r["bridge"] = 1.0 if r.get("bridge") else 0.0
        vals = [r.get(k) for k, _, _ in PREDICTORS]
        if any(v is None for v in vals):
            continue
        X.append(vals)
        keep.append(r)
    return np.array(X, float), keep


def blocks(rows, km):
    return np.array([f"{int(r['lat'] / (KM_LAT * km))}_{int(r['lon'] / (KM_LON * km))}" for r in rows])


def cv_r2(model_fn, X, y, splitter, groups=None):
    pred = np.zeros_like(y)
    for tr, te in splitter.split(X, y, groups):
        m = model_fn().fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    return float(r2_score(y, pred))


def main():
    sample = json.load(open(os.path.join(DATA, "citywide_sample.json")))
    X_all, rows_all = rows_to_matrix(sample)
    print("usable rows:", len(rows_all), "of", len(sample))

    out = {"predictors": [{"key": k, "label": l, "unit": u} for k, l, u in PREDICTORS],
           "targets": [{"key": k, "label": l, "unit": u, "res": res} for k, l, u, res in TARGETS],
           "n_sample": len(sample), "models": {}, "scatter": {}}

    # raw points for scatter plots (thin to keep the page light)
    out["points"] = [{"lat": r["lat"], "lon": r["lon"], "hw": r["hw"],
                      **{k: round(float(x), 3) for (k, _, _), x in zip(PREDICTORS, xx)},
                      **{t: (None if r.get(t) is None else round(r[t], 3)) for t, *_ in TARGETS}}
                     for r, xx in zip(rows_all, X_all)]

    # Spearman correlation matrix across predictors + targets
    keys = [k for k, *_ in PREDICTORS] + [t for t, *_ in TARGETS]
    M = np.array([[p[k] if p[k] is not None else np.nan for k in keys] for p in out["points"]], float)
    ranks = np.array([np.argsort(np.argsort(np.where(np.isnan(c), np.inf, c))) for c in M.T], float).T
    corr = np.full((len(keys), len(keys)), np.nan)
    for i in range(len(keys)):
        for j in range(len(keys)):
            ok = ~np.isnan(M[:, i]) & ~np.isnan(M[:, j])
            a, b = M[ok, i], M[ok, j]
            ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
            corr[i, j] = np.corrcoef(ra, rb)[0, 1]
    out["corr"] = {"keys": keys, "matrix": np.round(corr, 3).tolist()}

    # VIF (multicollinearity) on predictors
    Xs = StandardScaler().fit_transform(X_all)
    vif = []
    for j in range(Xs.shape[1]):
        others = np.delete(Xs, j, axis=1)
        r2 = LinearRegression().fit(others, Xs[:, j]).score(others, Xs[:, j])
        vif.append(round(1 / max(1e-6, 1 - r2), 2))
    out["vif"] = vif

    for tkey, tlabel, unit, _ in TARGETS:
        idx = [i for i, r in enumerate(rows_all) if r.get(tkey) is not None]
        X, y = X_all[idx], np.array([rows_all[i][tkey] for i in idx], float)
        rows = [rows_all[i] for i in idx]
        sc = StandardScaler().fit(X)
        Z = sc.transform(X)
        g1 = blocks(rows, 1)
        g2 = blocks(rows, 2)

        # OLS with standardized predictors, SEs clustered by 1 km cell
        ols = sm.OLS(y, sm.add_constant(Z)).fit(cov_type="cluster", cov_kwds={"groups": np.unique(g1, return_inverse=True)[1]})
        ci = ols.conf_int(0.05)
        coefs = [{"key": k, "label": l, "beta": round(float(ols.params[j + 1]), 4),
                  "lo": round(float(ci[j + 1][0]), 4), "hi": round(float(ci[j + 1][1]), 4),
                  "p": float(ols.pvalues[j + 1]),
                  "per_unit": round(float(ols.params[j + 1] / sc.scale_[j]), 5)}
                 for j, (k, l, _) in enumerate(PREDICTORS)]

        # bivariate fits (raw units) + binned means for each predictor
        biv = {}
        for j, (k, l, u) in enumerate(PREDICTORS):
            x = X[:, j]
            if np.std(x) == 0:
                continue
            slope, icept = np.polyfit(x, y, 1)
            r = float(np.corrcoef(x, y)[0, 1])
            rs = float(np.corrcoef(np.argsort(np.argsort(x)), np.argsort(np.argsort(y)))[0, 1])
            fit1 = sm.OLS(y, sm.add_constant(x)).fit(cov_type="cluster", cov_kwds={"groups": np.unique(g1, return_inverse=True)[1]})
            if k == "bridge":
                bins = [{"x": v, "y": round(float(y[x == v].mean()), 3), "n": int((x == v).sum())} for v in (0.0, 1.0)]
            else:
                qs = np.unique(np.quantile(x, np.linspace(0, 1, 11)))
                bins = []
                for a, b in zip(qs[:-1], qs[1:]):
                    m = (x >= a) & (x <= b)
                    if m.sum() >= 15:
                        bins.append({"x": round(float(x[m].mean()), 3), "y": round(float(y[m].mean()), 3), "n": int(m.sum()),
                                     "se": round(float(y[m].std() / math.sqrt(m.sum())), 3)})
            biv[k] = {"slope": float(slope), "icept": float(icept), "r": round(r, 3), "rho": round(rs, 3),
                      "p": float(fit1.pvalues[1]), "bins": bins}

        # predictive skill: random CV vs spatial-block CV, linear vs gradient boosting
        rnd = KFold(5, shuffle=True, random_state=0)
        spa = GroupKFold(5)
        gbm = lambda: HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, random_state=0)
        perf = {
            "ols_in_sample": round(float(ols.rsquared), 3),
            "ols_random_cv": round(cv_r2(LinearRegression, Z, y, rnd), 3),
            "ols_spatial_cv": round(cv_r2(LinearRegression, Z, y, spa, g2), 3),
            "gbm_random_cv": round(cv_r2(gbm, X, y, rnd), 3),
            "gbm_spatial_cv": round(cv_r2(gbm, X, y, spa, g2), 3),
        }
        # permutation importance for the boosted model, on held-out spatial blocks
        imp = np.zeros(X.shape[1])
        for tr, te in spa.split(X, y, g2):
            m = gbm().fit(X[tr], y[tr])
            imp += permutation_importance(m, X[te], y[te], n_repeats=5, random_state=0).importances_mean
        imp /= 5
        out["models"][tkey] = {"n": len(y), "coefs": coefs, "bivariate": biv, "perf": perf,
                               "importance": [round(float(v), 4) for v in imp],
                               "mean": round(float(y.mean()), 3), "sd": round(float(y.std()), 3)}
        print(tkey, "n", len(y), perf)

    # logistic model: "cold road" = coldest 25% by Landsat winter surface temperature
    idx = [i for i, r in enumerate(rows_all) if r.get("lst_day_anom_f") is not None]
    X, yv = X_all[idx], np.array([rows_all[i]["lst_day_anom_f"] for i in idx])
    rows = [rows_all[i] for i in idx]
    cut = float(np.quantile(yv, 0.25))
    yb = (yv <= cut).astype(int)
    sc = StandardScaler().fit(X)
    Z = sc.transform(X)
    g2 = blocks(rows, 2)
    probs = np.zeros(len(yb))
    for tr, te in GroupKFold(5).split(Z, yb, g2):
        probs[te] = LogisticRegression(max_iter=500).fit(Z[tr], yb[tr]).predict_proba(Z[te])[:, 1]
    auc_spatial = float(roc_auc_score(yb, probs))
    logit = sm.Logit(yb, sm.add_constant(Z)).fit(disp=0, cov_type="cluster",
                                                 cov_kwds={"groups": np.unique(blocks(rows, 1), return_inverse=True)[1]})
    lci = logit.conf_int(0.05)
    out["logistic"] = {
        "cut_f": round(cut, 2), "n": len(yb), "auc_spatial_cv": round(auc_spatial, 3),
        "odds": [{"key": k, "label": l, "or": round(math.exp(logit.params[j + 1]), 3),
                  "lo": round(math.exp(lci[j + 1][0]), 3), "hi": round(math.exp(lci[j + 1][1]), 3),
                  "p": float(logit.pvalues[j + 1])} for j, (k, l, _) in enumerate(PREDICTORS)],
    }
    # calibration: observed cold share by predicted-probability decile (held-out)
    order = np.argsort(probs)
    out["logistic"]["calibration"] = [{"pred": round(float(probs[c].mean()), 3), "obs": round(float(yb[c].mean()), 3), "n": len(c)}
                                      for c in np.array_split(order, 10)]
    print("logistic AUC (spatial CV):", round(auc_spatial, 3))

    # apply the logistic model to the hospital corridors and compare with the physics engine
    final = LogisticRegression(max_iter=500).fit(Z, yb)
    rep = json.load(open(os.path.join(DATA, "replay.json")))
    hosp = {h["id"]: h["name"] for h in rep["hospitals"]}
    segs = [s for s in rep["segments"] if s.get("gee")]
    Xh, keep = rows_to_matrix([{**s["gee"], "bridge": s["bridge"], "lat": s["coords"][0][0], "lon": s["coords"][0][1]} for s in segs])
    ph = final.predict_proba(sc.transform(Xh))[:, 1]
    ranked = sorted(zip(ph, segs), key=lambda t: -t[0])
    out["hospital_top"] = [{"name": s["name"], "hosp": hosp[s["hosp"]], "bridge": s["bridge"], "p": round(float(p), 3),
                            "cold_idx": s["cold_idx"], "canopy": s["gee"].get("canopy_pct"), "imperv": s["gee"].get("impervious_pct"),
                            "built_h": s["gee"].get("built_h_m")} for p, s in ranked[:15]]
    ce = [-s["cold_idx"] for s in segs]   # engine: negative cold_idx = colder, so flip -> + means agreement
    rho = float(np.corrcoef(np.argsort(np.argsort(ph)), np.argsort(np.argsort(ce)))[0, 1])
    out["engine_agreement"] = {"rho": round(rho, 3), "n": len(segs)}
    out["hospital_by_area"] = []
    for hid, name in hosp.items():
        pp = [p for p, s in zip(ph, segs) if s["hosp"] == hid]
        if pp:
            out["hospital_by_area"].append({"hosp": name, "mean_p": round(float(np.mean(pp)), 3), "n": len(pp)})
    print("engine vs data-driven model, Spearman:", round(rho, 3))

    open(os.path.join(DATA, "regression.js"), "w").write("window.REG=" + json.dumps(out, separators=(",", ":")) + ";")
    print("wrote data/regression.js")


if __name__ == "__main__":
    main()
