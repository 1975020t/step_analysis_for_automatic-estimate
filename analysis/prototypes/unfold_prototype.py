"""REFERENCE PROTOTYPE (not production code): general unfold by walking the face graph.

Walks the skin faces of ONE side of the sheet from the largest planar face. Every time the walk leaves
a bend (cylinder face) it composes the unfold transform
    U = Translate(n * BA) o Rotate(bend axis, -theta),   BA = theta * (r_inner + K * t)
so each planar face (outer and hole wires) lands in the base plane. Bend regions become strips between
their two tangent edges. The union of all regions is the flat pattern; holes = interior loops.

Measured on golden_v1 Lv1-Lv4 (407 parts): see analysis/handoff_analysis_logic.md.
Run from the repo root:  python analysis/prototypes/unfold_prototype.py [part names...]
Known gaps: no self-verification, no status/confidence, faces are sampled (24 points per edge),
no handling of bends crossed by holes, closed loops (boxes) take the first path found.
"""
import json, math, sys, time, numpy as np, cadquery as cq
from collections import deque
from shapely.geometry import Polygon, MultiPoint
from shapely.ops import unary_union
sys.path.insert(0,".")
from src.sheetmetal_recognition import SheetMetalRecognition
from src.sheetmetal_geometry import build_topology_graph, ToleranceContext

def rot(P, D, ang):
    d=np.asarray(D)/np.linalg.norm(D); K=np.array([[0,-d[2],d[1]],[d[2],0,-d[0]],[-d[1],d[0],0]])
    R=np.eye(3)+math.sin(ang)*K+(1-math.cos(ang))*K@K
    M=np.eye(4); M[:3,:3]=R; M[:3,3]=np.asarray(P)-R@np.asarray(P); return M
def trans(v): M=np.eye(4); M[:3,3]=v; return M
def ap(M,p): return (M@np.array([*p,1.0]))[:3]
def vec(v): return np.array(v.toTuple())

def unfold(path, K=0.5):
    solid=cq.importers.importStep(path).val(); faces=list(solid.Faces())
    rec=SheetMetalRecognition(ToleranceContext.from_shape(solid).linear_mm)
    t=rec.select_thickness(rec.surface_thickness_candidates(faces)); pairs=rec.match_surface_pairs(faces,t)
    topo=build_topology_graph(faces)
    skin={i for p in pairs for i in (p.first,p.second)}
    rin={}
    for p in pairs:
        if p.kind=="CYLINDER":
            r=min(rec.cylinder(faces[p.first])[2],rec.cylinder(faces[p.second])[2]); rin[p.first]=rin[p.second]=r
    base=max((p for p in pairs if p.kind=="PLANE"), key=lambda p: faces[p.first].Area())
    start=base.first; n0=vec(faces[start].normalAt()); c0=vec(faces[start].Center())
    T={start:np.eye(4)}; parent={}; q=deque([start]); strips=[]
    while q:
        i=q.popleft(); gi=faces[i].geomType()
        for j in topo.nodes[i].adjacent:
            if j in T or j not in skin: continue
            gj=faces[j].geomType()
            if gi=="PLANE" and gj=="CYLINDER":
                T[j]=T[i]; parent[j]=i; q.append(j)
            elif gi=="CYLINDER" and gj=="PLANE":
                p,d,_=rec.cylinder(faces[i]); Ti=T[i]; P=ap(Ti,p); D=Ti[:3,:3]@np.array(d)
                nj=Ti[:3,:3]@vec(faces[j].normalAt()); cj=ap(Ti,vec(faces[j].Center()))
                # rotation angle that makes child plane parallel to base plane (keeping sheet side)
                ref=Ti[:3,:3]@vec(faces[parent[i]].normalAt())
                a=math.atan2(np.dot(np.cross(nj,ref),D/np.linalg.norm(D)), np.dot(nj,ref))
                R=rot(P,D,a)
                theta=abs(a); BA=theta*(rin[i]+K*t)
                cc=ap(R,cj); foot=P+np.dot(cc-P,D)*D/np.dot(D,D); v=cc-foot; v-=np.dot(v,n0)*n0; v/=np.linalg.norm(v)
                T[j]=trans(v*BA)@R@Ti; parent[j]=i; q.append(j)
                # bend strip: tangent edge on parent side (frame Ti) and on child side (frame T[j])
                e_par=[e for e in faces[i].Edges() if e.geomType()=="LINE" and hash(e) in topo.nodes[parent[i]].shared_edges]
                e_chi=[e for e in faces[i].Edges() if e.geomType()=="LINE" and hash(e) in topo.nodes[j].shared_edges]
                pts=[ap(Ti,vec(v)) for e in e_par for v in (e.startPoint(),e.endPoint())]+[ap(T[j],vec(v)) for e in e_chi for v in (e.startPoint(),e.endPoint())]
                strips.append(pts)
            elif gi==gj=="PLANE":
                T[j]=T[i]; parent[j]=i; q.append(j)
    x=np.cross([1,0,0] if abs(n0[0])<.9 else [0,1,0], n0); x/=np.linalg.norm(x); y=np.cross(n0,x)
    to2=lambda pt:(float(np.dot(pt-c0,x)),float(np.dot(pt-c0,y)))
    regions=[]; holes=[]
    for i,Mi in T.items():
        if faces[i].geomType()!="PLANE": continue
        ws=faces[i].Wires(); outer=faces[i].outerWire()
        for w in ws:
            pts=[to2(ap(Mi,vec(v))) for e in w.Edges() for v in e.positions(np.linspace(0,1,24)[:-1])]
            poly=Polygon(pts).buffer(0)
            (regions if w.isSame(outer) else holes).append(poly)
    for s in strips:
        regions.append(MultiPoint([to2(p) for p in s]).convex_hull)
    eps=0.01  # closes sub-0.01 mm slivers between bend strips and planar regions (else cut length is inflated)
    flat=unary_union([g.buffer(eps,join_style=2) for g in regions]).buffer(-eps,join_style=2)
    flat=flat.difference(unary_union(holes)) if holes else flat
    return flat, t, sum(faces[i].geomType()=="PLANE" for i in T), sum(p.kind=="PLANE" for p in pairs)

if __name__=="__main__":
    idx=[p for p in json.load(open("golden/datasets/golden_v1.json"))["parts"]]
    import random; rng=random.Random(0)
    names=sys.argv[1:] or [p["name"] for p in idx if p["level"]!="Lv0"]
    by={p["name"]:p for p in idx}; ok=0; tot=0; worst=[]; per={}
    for n in names:
        tr=by[n]; folder="curated" if n.startswith("G") else tr["level"]
        t0=time.time(); flat,t,got_pl,exp_pl=unfold(f"golden_data/{folder}/{n}.step")
        ea=(flat.area-tr["blank_area_mm2"])/tr["blank_area_mm2"]
        cut=flat.length if flat.geom_type=="Polygon" else float("nan")
        ec=(cut-tr["cut_length_mm"])/tr["cut_length_mm"]
        holes=len(flat.interiors) if flat.geom_type=="Polygon" else -1
        good=flat.geom_type=="Polygon" and abs(ea)<=0.10 and abs(ec)<=0.10 and holes==tr["hole_count"]
        ok+=good; tot+=1; c=per.setdefault(tr['level'],[0,0]); c[0]+=good; c[1]+=1
        if not good or len(names)<10: print(f"{n} {flat.geom_type} area {ea:+.2%} cut {ec:+.2%} holes {holes}/{tr['hole_count']} planes {got_pl}/{exp_pl} {time.time()-t0:.1f}s")
    print(f"within 10% & holes exact: {ok}/{tot}")
    for lv in sorted(per): print(lv, f"{per[lv][0]}/{per[lv][1]}")
