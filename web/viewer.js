// Interactive 3D view of a built layout: the glTF the Python side exports, in three.js.
// Drag to rotate, wheel to zoom, right-drag to pan; hovering a part names it.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const loader = new GLTFLoader();

export function mountViewer(container, url, onError) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.9;
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf1f3f5);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.55; // the room is bright: keep black anodizing black
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(-500, 900, -350); // VTK's key light (-350, -500, 900), in the glTF frame (see VTK below)
  scene.add(key, new THREE.HemisphereLight(0xffffff, 0x8a9096, 0.25));

  const camera = new THREE.PerspectiveCamera(30, 1, 1, 20000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.45; // stay above the table

  const tip = document.createElement("div");
  tip.className = "tip3d";
  tip.hidden = true;
  container.appendChild(tip);

  let disposed = false;
  let root = null;
  const pickable = [];

  function size() {
    const w = container.clientWidth, h = container.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(h, 1);
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(size);
  ro.observe(container);
  size();

  function frame() {
    // the still picture's view: from the front-left, a little above (optolayout.view3d.frame_board
    // looks along VTK (-0.45, -1.0, 0.9)). pyvista's glTF export maps VTK (x, y, z) to (y, z, x):
    // z stays up, glTF x is VTK y (towards the back of the board), glTF z is VTK x (to the right).
    const box = new THREE.Box3().setFromObject(root);
    const c = box.getCenter(new THREE.Vector3());
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    const dir = new THREE.Vector3(-1.0, 0.9, -0.45).normalize();
    const dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 0.68;
    controls.target.copy(c);
    camera.position.copy(c).addScaledVector(dir, dist);
    camera.near = dist / 100;
    camera.far = dist * 20;
    camera.updateProjectionMatrix();
    controls.update();
  }

  loader.load(
    url,
    (gltf) => {
      if (disposed) return;
      root = gltf.scene;
      root.traverse((o) => {
        if (o.isMesh && o.userData && o.userData.label) pickable.push(o);
      });
      scene.add(root);
      frame();
    },
    undefined,
    (err) => onError && onError(err),
  );

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function hover(e) {
    if (!root) return;
    const rect = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(pickable, false)[0];
    if (hit) {
      tip.textContent = hit.object.userData.label;
      tip.style.left = `${e.clientX - rect.left + 12}px`;
      tip.style.top = `${e.clientY - rect.top + 12}px`;
      tip.hidden = false;
    } else tip.hidden = true;
  }
  renderer.domElement.addEventListener("pointermove", hover);
  renderer.domElement.addEventListener("pointerleave", () => (tip.hidden = true));

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  return {
    reset: () => root && frame(),
    dispose() {
      disposed = true;
      renderer.setAnimationLoop(null);
      ro.disconnect();
      controls.dispose();
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) [].concat(o.material).forEach((m) => m.dispose());
      });
      pmrem.dispose();
      renderer.dispose();
      container.innerHTML = "";
    },
  };
}

window.OptViewer = { mountViewer };
window.dispatchEvent(new Event("optviewer-ready"));
