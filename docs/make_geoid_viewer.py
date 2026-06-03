"""Interactive three.js viewer for the geoid 'potato' — pick a camera angle.

Renders the EGM2008 field as a sphere displaced radially by the geoid undulation N, colored by N,
with a ghost reference sphere. Orbit to rotate, slider for exaggeration, live elev/azim readout
that maps 1:1 to make_geoid_3d.py's --elev/--azim.

  python docs/make_geoid_viewer.py --out docs/visuals/geoid_viewer.html
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, "pipeline")
import geoid as G


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Geoid — 3D</title>
<script src="https://unpkg.com/three@0.128.0/build/three.min.js"></script>
<script src="https://unpkg.com/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<style>
  html,body{margin:0;height:100%;background:#0b1722;overflow:hidden;font-family:-apple-system,system-ui,sans-serif}
  #c{width:100vw;height:100vh;display:block}
  #panel{position:fixed;top:14px;left:14px;background:rgba(16,22,30,.86);color:#e7eef5;padding:14px 16px;
         border-radius:10px;font-size:13px;min-width:230px;line-height:1.7;box-shadow:0 4px 18px rgba(0,0,0,.4)}
  #panel h3{margin:0 0 8px;font-size:14px}
  #panel label{display:block;margin-top:8px}
  input[type=range]{width:100%}
  .val{color:#7fd1ff;font-weight:600}
  #readout{margin-top:12px;padding-top:10px;border-top:1px solid #2a3744;font-family:ui-monospace,monospace;font-size:12px;color:#9fb3c8}
  #readout b{color:#7fd1ff}
  #err{position:fixed;bottom:0;left:0;right:0;background:#7a1f1f;color:#fff;padding:8px 12px;font-family:ui-monospace,monospace;font-size:12px;display:none}
</style></head><body>
<canvas id="c"></canvas>
<div id="panel">
  <h3>EGM2008 geoid — 3D</h3>
  <label>Vertical exaggeration: <span class="val" id="ev">13000</span>×
    <input type="range" id="exag" min="2000" max="30000" step="1000" value="13000"></label>
  <label><input type="checkbox" id="ghost" checked> ghost reference sphere</label>
  <div style="margin-top:8px;color:#9fb3c8;font-size:12px">drag = rotate · scroll = zoom</div>
  <div id="readout"></div>
</div>
<div id="err"></div>
<script>
window.onerror=function(m,s,l,c){var d=document.getElementById('err');d.style.display='block';d.textContent='JS error: '+m+' ('+l+':'+c+')';};
const N=__DATA__, NLAT=__NLAT__, NLON=__NLON__, EMIN=__EMIN__, EMAX=__EMAX__, R=6371000;
const SP=[[0,94,79,162],[.1,50,136,189],[.2,102,194,165],[.3,171,221,164],[.4,230,245,152],
          [.5,255,255,191],[.6,254,224,139],[.7,253,174,97],[.8,244,109,67],[.9,213,62,79],[1,158,1,66]];
function col(n){let t=Math.max(0,Math.min(1,(n-EMIN)/(EMAX-EMIN)));
  for(let i=1;i<SP.length;i++){if(t<=SP[i][0]){let a=SP[i-1],b=SP[i],f=(t-a[0])/(b[0]-a[0]);
    return [(a[1]+f*(b[1]-a[1]))/255,(a[2]+f*(b[2]-a[2]))/255,(a[3]+f*(b[3]-a[3]))/255];}}return[.62,0,.26];}
let exag=13000;
const scene=new THREE.Scene(); scene.background=new THREE.Color(0x0b1722);
const camera=new THREE.PerspectiveCamera(32, innerWidth/innerHeight, .1, 100); camera.up.set(0,0,1);
camera.position.set(2.52,-1.76,0.88);
const renderer=new THREE.WebGLRenderer({canvas:document.getElementById('c'),antialias:true});
renderer.setSize(innerWidth,innerHeight); renderer.setPixelRatio(devicePixelRatio);
const controls=new THREE.OrbitControls(camera, renderer.domElement); controls.enablePan=false;
scene.add(new THREE.AmbientLight(0xffffff,.6));
const dl=new THREE.DirectionalLight(0xffffff,.75); dl.position.set(1.2,-.6,1); scene.add(dl);
function geometry(){
  const g=new THREE.BufferGeometry(), P=new Float32Array(NLAT*NLON*3), C=new Float32Array(NLAT*NLON*3);
  for(let i=0;i<NLAT;i++){const lat=(-89.5+179*i/(NLAT-1))*Math.PI/180;
    for(let j=0;j<NLON;j++){const lon=(-180+360*j/(NLON-1))*Math.PI/180,n=N[i*NLON+j],r=1+n*exag/R,k=(i*NLON+j)*3;
      P[k]=r*Math.cos(lat)*Math.cos(lon);P[k+1]=r*Math.cos(lat)*Math.sin(lon);P[k+2]=r*Math.sin(lat);
      const c=col(n);C[k]=c[0];C[k+1]=c[1];C[k+2]=c[2];}}
  const I=[];for(let i=0;i<NLAT-1;i++)for(let j=0;j<NLON-1;j++){
    const a=i*NLON+j,b=a+1,c=a+NLON,d=c+1;I.push(a,b,d,a,d,c);}
  g.setAttribute('position',new THREE.BufferAttribute(P,3));
  g.setAttribute('color',new THREE.BufferAttribute(C,3));
  g.setIndex(I); g.computeVertexNormals(); return g;
}
const mesh=new THREE.Mesh(geometry(), new THREE.MeshStandardMaterial({vertexColors:true,roughness:.9,metalness:0})); scene.add(mesh);
const ghost=new THREE.Mesh(new THREE.SphereGeometry(1,64,40), new THREE.MeshBasicMaterial({color:0x9aa3ad,wireframe:true,transparent:true,opacity:.18})); scene.add(ghost);
function rebuild(){mesh.geometry.dispose(); mesh.geometry=geometry();}
document.getElementById('exag').oninput=e=>{exag=+e.target.value;document.getElementById('ev').textContent=exag;rebuild();};
document.getElementById('ghost').onchange=e=>{ghost.visible=e.target.checked;};
addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);});
const ro=document.getElementById('readout');
function tick(){requestAnimationFrame(tick);controls.update();renderer.render(scene,camera);
  const p=camera.position,D=p.length(),elev=Math.asin(p.z/D)*180/Math.PI,azim=Math.atan2(p.y,p.x)*180/Math.PI;
  ro.innerHTML=`elev <b>${elev.toFixed(0)}</b>°  azim <b>${azim.toFixed(0)}</b>°<br>exaggeration <b>${exag.toLocaleString()}</b>×`;}
tick();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/visuals/geoid_viewer.html")
    ap.add_argument("--step", type=float, default=1.0, help="grid spacing in degrees")
    a = ap.parse_args()
    lat = np.arange(-89.5, 90, a.step)
    lon = np.arange(-180, 180.001, a.step)
    LON, LAT = np.meshgrid(lon, lat)
    N = G.undulation_for_latlon(LAT.ravel(), LON.ravel()).reshape(LAT.shape)
    Ni = np.rint(N).astype(int)
    html = (HTML.replace("__DATA__", json.dumps(Ni.ravel().tolist(), separators=(",", ":")))
                .replace("__NLAT__", str(len(lat))).replace("__NLON__", str(len(lon)))
                .replace("__EMIN__", str(int(N.min()))).replace("__EMAX__", str(int(N.max()))))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(html)
    print(f"wrote {a.out}  ({os.path.getsize(a.out)/1e6:.1f} MB; grid {len(lat)}x{len(lon)}, N {int(N.min())}..{int(N.max())} m)")


if __name__ == "__main__":
    main()
