"""Build a self-contained interactive deck.gl 3D viewer for the Aspen H3 cluster.

Footprint is the H3 hierarchy: a ring of res-4 cells over the Colorado Rockies (Aspen at center),
shown at res 4-8 (each cell subdividing into its 7 children). Extrudes by elevation; mouse
rotate/tilt/zoom; sliders for vertical exaggeration and resolution; live camera readout.
Hexagon outlines are precomputed and drawn with deck.gl's core SolidPolygonLayer (no runtime H3).

  python docs/make_3d_viewer.py --dir h3-terrain --out docs/visuals/aspen_3d_viewer.html
"""
import argparse
import json
import os

import h3
import numpy as np
import pyarrow.parquet as pq

KASE = (39.2232, -106.8687)


def children_polys(D, res, parents):
    if res == 4:
        cells = np.array([h3.str_to_int(c) for c in parents], dtype="uint64")
    else:
        cells = np.array([h3.str_to_int(c) for p in parents for c in h3.cell_to_children(p, res)],
                         dtype="uint64")
    single = f"{D}/res{res}/part-000.parquet"
    if os.path.exists(single):
        groups = {0: cells}; path = lambda b: single
    else:
        base = (cells >> 45) & 127
        groups = {int(b): cells[base == b] for b in np.unique(base)}
        path = lambda b: f"{D}/res{res}/base={b}/part-000.parquet"
    oc, oe = [], []
    for b, tc in groups.items():
        t = pq.ParquetFile(path(b)).read(columns=["h3_cell", "elevation_mean"])
        sc = t.column("h3_cell").to_numpy(); se = t.column("elevation_mean").to_numpy()
        o = np.argsort(sc); sc, se = sc[o], se[o]
        idx = np.clip(np.searchsorted(sc, tc), 0, len(sc) - 1)
        ok = sc[idx] == tc
        oc.append(tc[ok]); oe.append(se[idx[ok]])
    cells = np.concatenate(oc); elev = np.concatenate(oe)
    return [{"p": [[round(lo, 5), round(la, 5)] for la, lo in h3.cell_to_boundary(h3.int_to_str(int(c)))],
             "e": round(float(e), 1)} for c, e in zip(cells, elev)]


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Aspen H3 terrain — 3D</title>
<script src="https://unpkg.com/deck.gl@9.0.0/dist.min.js"></script>
<style>
  html,body{margin:0;height:100%;background:#0b1722;font-family:-apple-system,system-ui,sans-serif;overflow:hidden}
  #map{width:100vw;height:100vh;position:relative}
  #panel{position:fixed;top:14px;left:14px;background:rgba(16,22,30,.86);color:#e7eef5;
         padding:14px 16px;border-radius:10px;font-size:13px;min-width:252px;line-height:1.7;box-shadow:0 4px 18px rgba(0,0,0,.4);z-index:10}
  #panel h3{margin:0 0 8px;font-size:14px}
  #panel label{display:block;margin-top:8px}
  input[type=range]{width:100%}
  .val{color:#7fd1ff;font-weight:600}
  #readout{margin-top:12px;padding-top:10px;border-top:1px solid #2a3744;font-family:ui-monospace,monospace;font-size:12px;color:#9fb3c8}
  #readout b{color:#7fd1ff}
  select{background:#1b2430;color:#e7eef5;border:1px solid #2a3744;border-radius:5px;padding:3px}
  #err{position:fixed;bottom:0;left:0;right:0;background:#7a1f1f;color:#fff;padding:8px 12px;
       font-family:ui-monospace,monospace;font-size:12px;display:none;z-index:20}
</style></head><body>
<div id="map"></div>
<div id="panel">
  <h3>Aspen &amp; the Elk Mountains — H3 terrain, 3D</h3>
  <label>Vertical exaggeration: <span class="val" id="exagval">25</span>×
    <input type="range" id="exag" min="1" max="120" value="25"></label>
  <label>Resolution:
    <select id="res"><option value="4">res 4 (23 km)</option>
      <option value="5">res 5 (8.5 km)</option><option value="6">res 6 (3.2 km)</option>
      <option value="7">res 7 (1.2 km)</option><option value="8" selected>res 8 (460 m)</option></select></label>
  <div style="margin-top:8px;color:#9fb3c8;font-size:12px">drag = pan · ctrl/right-drag = rotate &amp; tilt · scroll = zoom</div>
  <div id="readout"></div>
</div>
<div id="err"></div>
<script>
window.onerror=function(m,s,l,c){var d=document.getElementById('err');d.style.display='block';
  d.textContent='JS error: '+m+'  ('+l+':'+c+')';};
const DATA = __DATA__;
const EMIN = __EMIN__, EMAX = __EMAX__;
const STOPS=[[0,39,96,74],[.12,63,138,79],[.32,167,193,99],[.52,226,200,121],[.70,176,122,67],[.86,122,82,48],[1,255,255,255]];
function color(e){let t=Math.max(0,Math.min(1,(e-EMIN)/(EMAX-EMIN)));
  for(let i=1;i<STOPS.length;i++){if(t<=STOPS[i][0]){let a=STOPS[i-1],b=STOPS[i],f=(t-a[0])/(b[0]-a[0]);
    return [a[1]+f*(b[1]-a[1]),a[2]+f*(b[2]-a[2]),a[3]+f*(b[3]-a[3])];}}return[255,255,255];}
let res='8', exag=25;
let view={longitude:__LNG__,latitude:__LAT__,zoom:8.0,pitch:37,bearing:111,maxPitch:85};
function layer(){return new deck.SolidPolygonLayer({id:'hex-'+res,data:DATA[res],
  getPolygon:d=>d.p, extruded:true, getElevation:d=>d.e-EMIN, elevationScale:exag,
  getFillColor:d=>color(d.e), material:{ambient:.55,diffuse:.6,shininess:18,specularColor:[40,40,40]},
  pickable:false});}
let dk;
try{
  dk=new deck.DeckGL({container:'map',initialViewState:view,controller:true,
    onViewStateChange:({viewState})=>{view=viewState;readout();},layers:[layer()]});
}catch(err){window.onerror(err.message,'',0,0);}
function render(){if(dk)dk.setProps({layers:[layer()]});}
function readout(){document.getElementById('readout').innerHTML=
  `pitch <b>${(+view.pitch).toFixed(0)}</b>°  bearing <b>${(+view.bearing).toFixed(0)}</b>°  zoom <b>${(+view.zoom).toFixed(1)}</b><br>`+
  `exaggeration <b>${exag}</b>×  ·  res <b>${res}</b>  ·  cells <b>${DATA[res].length.toLocaleString()}</b>`;}
document.getElementById('exag').oninput=e=>{exag=+e.target.value;document.getElementById('exagval').textContent=exag;render();readout();};
document.getElementById('res').onchange=e=>{res=e.target.value;render();readout();};
readout();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="h3-terrain")
    ap.add_argument("--out", default="docs/visuals/aspen_3d_viewer.html")
    a = ap.parse_args()
    parents = h3.grid_disk(h3.latlng_to_cell(*KASE, 4), 1)
    data = {str(r): children_polys(a.dir, r, parents) for r in (4, 5, 6, 7, 8)}
    clat, clng = h3.cell_to_latlng(h3.latlng_to_cell(*KASE, 4))
    allev = [d["e"] for r in data.values() for d in r]
    emin, emax = min(allev), max(allev)
    html = (HTML.replace("__DATA__", json.dumps(data, separators=(",", ":")))
                .replace("__EMIN__", str(emin)).replace("__EMAX__", str(emax))
                .replace("__LAT__", f"{clat:.5f}").replace("__LNG__", f"{clng:.5f}"))
    with open(a.out, "w") as f:
        f.write(html)
    print(f"wrote {a.out}  ({os.path.getsize(a.out)/1e6:.1f} MB; "
          f"cells per res: " + ", ".join(f"{r}={len(data[r])}" for r in data) + ")")


if __name__ == "__main__":
    main()
