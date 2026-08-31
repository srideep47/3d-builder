/* ModelViewer — three.js GLB viewer with orbit controls, ground grid,
   soft shadows and camera auto-framing. All assets served locally. */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export class ModelViewer {
  constructor(container) {
    this.container = container;
    this.model = null;
    this.autoRotate = false;
    this.wireframe = false;

    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.005, 100);
    this.camera.position.set(1.4, 1.0, 1.8);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.52;

    // Lighting: studio three-point rig.
    this.scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x1a1d26, 0.85));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(3, 6, 4);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0004;
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x9ec9ff, 0.7);
    fill.position.set(-4, 3, -2);
    this.scene.add(fill);
    const rim = new THREE.DirectionalLight(0xffe9c9, 0.55);
    rim.position.set(1, 2.5, -5);
    this.scene.add(rim);

    // Ground: subtle grid + shadow catcher.
    this.grid = new THREE.GridHelper(2, 20, 0x2c3a52, 0x1a2233);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.55;
    this.scene.add(this.grid);

    const shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(20, 20),
      new THREE.ShadowMaterial({ opacity: 0.35 })
    );
    shadowPlane.rotation.x = -Math.PI / 2;
    shadowPlane.receiveShadow = true;
    this.scene.add(shadowPlane);

    this.loader = new GLTFLoader();
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(container);
    this._resize();
    this._raf = requestAnimationFrame(this._tick.bind(this));
  }

  load(url) {
    return new Promise((resolve, reject) => {
      this.loader.load(
        url,
        (gltf) => {
          this.setModel(gltf.scene);
          resolve(gltf);
        },
        undefined,
        (err) => reject(err)
      );
    });
  }

  setModel(object) {
    if (this.model) {
      this.scene.remove(this.model);
    }
    this.model = object;

    object.traverse((node) => {
      if (node.isMesh) {
        node.castShadow = true;
        node.receiveShadow = true;
        if (node.material) node.material.side = THREE.FrontSide;
      }
    });

    // Sit the model on the ground plane and center it in X/Y.
    const box = new THREE.Box3().setFromObject(object);
    const center = box.getCenter(new THREE.Vector3());
    object.position.x -= center.x;
    object.position.y -= box.min.y;
    object.position.z -= center.z;

    this.scene.add(object);
    this._applyWireframe();
    this.frame();
  }

  frame() {
    if (!this.model) return;
    const box = new THREE.Box3().setFromObject(this.model);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const center = box.getCenter(new THREE.Vector3());

    // Scale the grid to the model.
    const gridSize = Math.max(0.5, Math.ceil(maxDim * 2.4 * 2) / 2);
    this.scene.remove(this.grid);
    this.grid.geometry.dispose();
    this.grid = new THREE.GridHelper(gridSize, 24, 0x2c3a52, 0x1a2233);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.55;
    this.scene.add(this.grid);

    const dist = maxDim * 1.85;
    this.camera.position.set(center.x + dist * 0.75, center.y + dist * 0.55, center.z + dist * 0.9);
    this.camera.near = maxDim / 200;
    this.camera.far = maxDim * 60;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.update();
  }

  setWireframe(on) {
    this.wireframe = on;
    this._applyWireframe();
  }

  _applyWireframe() {
    if (!this.model) return;
    this.model.traverse((node) => {
      if (node.isMesh && node.material) {
        node.material.wireframe = this.wireframe;
      }
    });
  }

  setAutoRotate(on) {
    this.autoRotate = on;
    this.controls.autoRotate = on;
    this.controls.autoRotateSpeed = 2.2;
  }

  resetView() {
    this.frame();
  }

  _resize() {
    const w = this.container.clientWidth || 1;
    const h = this.container.clientHeight || 1;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  }

  _tick() {
    this._raf = requestAnimationFrame(this._tick.bind(this));
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    this._ro.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    if (this.container.contains(this.renderer.domElement)) {
      this.container.removeChild(this.renderer.domElement);
    }
  }
}
