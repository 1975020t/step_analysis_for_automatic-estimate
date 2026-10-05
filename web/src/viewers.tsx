import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { blobUrl } from "./api";

/** The STEP shape in 3D (GLB made by the API from the STEP file). Drag to rotate, wheel to zoom. */
export function Model3D({ fileId, height = 320 }: { fileId: string; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState("読み込み中…");
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let disposed = false;
    const width = el.clientWidth || 480;
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.setClearColor(0xfafafa);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(1, 2, 3);
    scene.add(sun);
    const camera = new THREE.PerspectiveCamera(35, width / height, 0.1, 100000);
    const controls = new OrbitControls(camera, renderer.domElement);
    let frame = 0;
    const loop = () => {
      if (disposed) return;
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(loop);
    };
    blobUrl(`/api/files/${fileId}/model.glb`)
      .then((url) => new GLTFLoader().loadAsync(url).finally(() => URL.revokeObjectURL(url)))
      .then((gltf) => {
        if (disposed) return;
        const model = gltf.scene;
        model.traverse((o: any) => {
          if (o.isMesh) o.material = new THREE.MeshStandardMaterial({ color: 0x9fb4cc, metalness: 0.35, roughness: 0.5, side: THREE.DoubleSide });
        });
        scene.add(model);
        const bounds = new THREE.Box3().setFromObject(model);
        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3()).length();
        controls.target.copy(center);
        camera.position.copy(center).add(new THREE.Vector3(size * 0.9, size * 0.7, size * 1.1));
        camera.near = size / 100;
        camera.far = size * 20;
        camera.updateProjectionMatrix();
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry((model.children[0] as any)?.geometry || new THREE.BufferGeometry(), 25),
          new THREE.LineBasicMaterial({ color: 0x33475e }),
        );
        if ((model.children[0] as any)?.geometry) {
          edges.applyMatrix4(model.children[0].matrixWorld);
          scene.add(edges);
        }
        setState("");
        loop();
      })
      .catch(() => setState("3D表示を作れませんでした"));
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, [fileId, height]);
  return (
    <div className="viewer" ref={box} style={{ height }} data-testid="model3d">
      {state && <div className="empty" style={{ position: "absolute", inset: 0 }}>{state}</div>}
    </div>
  );
}

/** The flat pattern from the analysis (outer and inner loops, bend lines), mm. */
export function FlatPattern({ flat, height = 260 }: { flat: any; height?: number }) {
  if (!flat || !(flat.outer_loops || []).length) return <div className="hatch" style={{ height }} />;
  const pts: number[][] = [...flat.outer_loops, ...(flat.inner_loops || [])].flat();
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const pad = Math.max(x1 - x0, y1 - y0) * 0.06;
  const path = (loop: number[][]) => loop.map((p, i) => `${i ? "L" : "M"}${p[0]},${-p[1]}`).join(" ") + "Z";
  return (
    <svg viewBox={`${x0 - pad} ${-y1 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`} style={{ width: "100%", height, background: "#fff", border: "1px solid var(--line)" }}
      data-testid="flat-pattern">
      <g fill="none" vectorEffect="non-scaling-stroke">
        {flat.outer_loops.map((l: number[][], i: number) => <path key={`o${i}`} d={path(l)} stroke="#22364c" strokeWidth={1.4} vectorEffect="non-scaling-stroke" fill="#eef3f9" />)}
        {(flat.inner_loops || []).map((l: number[][], i: number) => <path key={`i${i}`} d={path(l)} stroke="#22364c" strokeWidth={1.2} vectorEffect="non-scaling-stroke" fill="#fff" />)}
        {(flat.bend_lines || []).map((l: number[][], i: number) => (
          <path key={`b${i}`} d={l.map((p, j) => `${j ? "L" : "M"}${p[0]},${-p[1]}`).join(" ")} stroke="#c0392b" strokeDasharray="6 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
      </g>
    </svg>
  );
}
