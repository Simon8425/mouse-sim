import {
  type Vec3,
  type GeometryJson,
  type RigidTransformJson,
  IDENTITY_TRANSFORM,
} from '../api/contracts';

export interface Bounds3 {
  min: Vec3;
  max: Vec3;
}

/**
 * Transforms a point in local space by a rigid transform (R · p + t), where R
 * is a row-major 3×3 rotation matrix.
 */
export function transformPoint(transform?: RigidTransformJson | null, p?: Vec3): Vec3 {
  const t = transform ?? IDENTITY_TRANSFORM;
  const pt = p ?? [0, 0, 0];
  const [r0, r1, r2] = t.rotation;
  const [tx, ty, tz] = t.translation;
  const x = r0[0] * pt[0] + r0[1] * pt[1] + r0[2] * pt[2] + tx;
  const y = r1[0] * pt[0] + r1[1] * pt[1] + r1[2] * pt[2] + ty;
  const z = r2[0] * pt[0] + r2[1] * pt[1] + r2[2] * pt[2] + tz;
  return [x, y, z];
}

/**
 * Composes parent and child rigid transforms such that (P ∘ C)(p) = P(C(p)).
 * R_composed = R_parent · R_child
 * t_composed = R_parent · t_child + t_parent
 */
export function composeTransforms(
  parent?: RigidTransformJson | null,
  child?: RigidTransformJson | null,
): RigidTransformJson {
  const p = parent ?? IDENTITY_TRANSFORM;
  const c = child ?? IDENTITY_TRANSFORM;
  const pR = p.rotation;
  const cR = c.rotation;
  const r00 = pR[0][0] * cR[0][0] + pR[0][1] * cR[1][0] + pR[0][2] * cR[2][0];
  const r01 = pR[0][0] * cR[0][1] + pR[0][1] * cR[1][1] + pR[0][2] * cR[2][1];
  const r02 = pR[0][0] * cR[0][2] + pR[0][1] * cR[1][2] + pR[0][2] * cR[2][2];

  const r10 = pR[1][0] * cR[0][0] + pR[1][1] * cR[1][0] + pR[1][2] * cR[2][0];
  const r11 = pR[1][0] * cR[0][1] + pR[1][1] * cR[1][1] + pR[1][2] * cR[2][1];
  const r12 = pR[1][0] * cR[0][2] + pR[1][1] * cR[1][2] + pR[1][2] * cR[2][2];

  const r20 = pR[2][0] * cR[0][0] + pR[2][1] * cR[1][0] + pR[2][2] * cR[2][0];
  const r21 = pR[2][0] * cR[0][1] + pR[2][1] * cR[1][1] + pR[2][2] * cR[2][1];
  const r22 = pR[2][0] * cR[0][2] + pR[2][1] * cR[1][2] + pR[2][2] * cR[2][2];

  const tChildTransformed = transformPoint(p, c.translation);

  return {
    rotation: [
      [r00, r01, r02],
      [r10, r11, r12],
      [r20, r21, r22],
    ],
    translation: tChildTransformed,
    units: p.units,
  };
}

/**
 * Computes axis-aligned local bounds for a primitive or compound geometry.
 * Cylinder, cone, and frustum bases are at z = 0, extending to z = height.
 */
export function localBounds(geometry: GeometryJson): Bounds3 {
  switch (geometry.type) {
    case 'box': {
      const [sx, sy, sz] = geometry.size;
      return {
        min: [-sx / 2, -sy / 2, -sz / 2],
        max: [sx / 2, sy / 2, sz / 2],
      };
    }
    case 'sphere': {
      const r = geometry.radius;
      return { min: [-r, -r, -r], max: [r, r, r] };
    }
    case 'cylinder': {
      const r = geometry.radius;
      return { min: [-r, -r, 0], max: [r, r, geometry.height] };
    }
    case 'cone': {
      const r = geometry.base_radius;
      return { min: [-r, -r, 0], max: [r, r, geometry.height] };
    }
    case 'frustum': {
      const r = Math.max(geometry.top_radius, geometry.bottom_radius);
      return { min: [-r, -r, 0], max: [r, r, geometry.height] };
    }
    case 'mesh': {
      if (!geometry.vertices || geometry.vertices.length === 0) {
        return { min: [0, 0, 0], max: [0, 0, 0] };
      }
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (const v of geometry.vertices) {
        if (v[0] < minX) minX = v[0];
        if (v[1] < minY) minY = v[1];
        if (v[2] < minZ) minZ = v[2];
        if (v[0] > maxX) maxX = v[0];
        if (v[1] > maxY) maxY = v[1];
        if (v[2] > maxZ) maxZ = v[2];
      }
      return { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] };
    }
    case 'compound': {
      if (!geometry.children || geometry.children.length === 0) {
        return { min: [0, 0, 0], max: [0, 0, 0] };
      }
      let acc: Bounds3 | null = null;
      for (const child of geometry.children) {
        const cb = worldBounds(child);
        acc = acc ? unionBounds(acc, cb) : cb;
      }
      return acc ?? { min: [0, 0, 0], max: [0, 0, 0] };
    }
  }
}

/**
 * Transforms the 8 corners of an AABB by a rigid transform and returns the min/max AABB.
 */
export function applyTransformToBounds(transform: RigidTransformJson, b: Bounds3): Bounds3 {
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

  const corners: Vec3[] = [
    [b.min[0], b.min[1], b.min[2]],
    [b.max[0], b.min[1], b.min[2]],
    [b.min[0], b.max[1], b.min[2]],
    [b.max[0], b.max[1], b.min[2]],
    [b.min[0], b.min[1], b.max[2]],
    [b.max[0], b.min[1], b.max[2]],
    [b.min[0], b.max[1], b.max[2]],
    [b.max[0], b.max[1], b.max[2]],
  ];

  for (const corner of corners) {
    const pt = transformPoint(transform, corner);
    if (pt[0] < minX) minX = pt[0];
    if (pt[1] < minY) minY = pt[1];
    if (pt[2] < minZ) minZ = pt[2];
    if (pt[0] > maxX) maxX = pt[0];
    if (pt[1] > maxY) maxY = pt[1];
    if (pt[2] > maxZ) maxZ = pt[2];
  }

  return { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] };
}

/**
 * Calculates world-space bounds recursively.
 * Parity: Box size (2,4,6), rotation [[0,-1,0],[1,0,0],[0,0,1]], translation (1,2,3)
 * yields world min (−1,1,0), max (3,3,6).
 */
export function worldBounds(
  geometry: GeometryJson,
  parentTransform: RigidTransformJson = IDENTITY_TRANSFORM,
): Bounds3 {
  const currentTransform = composeTransforms(parentTransform, geometry.transform);
  if (geometry.type === 'compound') {
    let acc: Bounds3 | null = null;
    for (const child of geometry.children) {
      const cb = worldBounds(child, currentTransform);
      acc = acc ? unionBounds(acc, cb) : cb;
    }
    return acc ?? { min: [0, 0, 0], max: [0, 0, 0] };
  }
  const local = localBounds(geometry);
  return applyTransformToBounds(currentTransform, local);
}

export function boundsSize(b: Bounds3): Vec3 {
  return [b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]];
}

export function boundsCenter(b: Bounds3): Vec3 {
  return [
    (b.min[0] + b.max[0]) / 2,
    (b.min[1] + b.max[1]) / 2,
    (b.min[2] + b.max[2]) / 2,
  ];
}

export function boundsRadius(b: Bounds3): number {
  const [sx, sy, sz] = boundsSize(b);
  return Math.sqrt(sx * sx + sy * sy + sz * sz) / 2;
}

export function unionBounds(b1: Bounds3, b2: Bounds3): Bounds3 {
  return {
    min: [
      Math.min(b1.min[0], b2.min[0]),
      Math.min(b1.min[1], b2.min[1]),
      Math.min(b1.min[2], b2.min[2]),
    ],
    max: [
      Math.max(b1.max[0], b2.max[0]),
      Math.max(b1.max[1], b2.max[1]),
      Math.max(b1.max[2], b2.max[2]),
    ],
  };
}
