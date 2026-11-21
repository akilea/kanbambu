// Renders a small preview image of an STL file in the browser.
// Built into static/vendor/stl-thumb.js with tools/stl-thumb/build.sh (three.js, tree-shaken).
import {
  AmbientLight, Box3, Color, DirectionalLight, Mesh, MeshStandardMaterial,
  PerspectiveCamera, Scene, Sphere, Vector3, WebGLRenderer,
} from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

/** STL bytes -> { png: Blob, size: [x, y, z] in mm } */
export async function stlThumbnail(buffer, { px = 256, color = "#9aa3ad" } = {}) {
  const geom = new STLLoader().parse(buffer);
  geom.computeVertexNormals();
  geom.computeBoundingBox();
  const box = geom.boundingBox;
  const size = box.getSize(new Vector3());
  geom.translate(...box.getCenter(new Vector3()).multiplyScalar(-1).toArray());

  const scene = new Scene();
  scene.background = new Color("#eef0f3");
  // STL is Z-up (printer bed); three.js is Y-up
  const mesh = new Mesh(geom, new MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.05 }));
  mesh.rotation.x = -Math.PI / 2;
  scene.add(mesh);
  scene.add(new AmbientLight(0xffffff, 0.9));
  const key = new DirectionalLight(0xffffff, 1.6);
  key.position.set(1, 2, 1.5);
  scene.add(key);
  const rim = new DirectionalLight(0xffffff, 0.6);
  rim.position.set(-1.5, 0.5, -1);
  scene.add(rim);

  const r = new Box3().setFromObject(mesh).getBoundingSphere(new Sphere()).radius || 1;
  const cam = new PerspectiveCamera(30, 1, r / 100, r * 100);
  const d = r / Math.sin((30 / 2) * Math.PI / 180) * 1.05;
  cam.position.set(d * 0.55, d * 0.55, d * 0.63);
  cam.lookAt(0, 0, 0);

  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = px;
  const renderer = new WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setSize(px, px, false);
  renderer.render(scene, cam);
  const png = await new Promise((res) => canvas.toBlob(res, "image/png"));
  renderer.dispose();
  geom.dispose();
  return { png, size: [size.x, size.y, size.z] };
}
