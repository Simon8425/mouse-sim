"""Deterministic, dependency-free geometry and solid-property primitives.

All lengths stored by this module are SI metres.  Constructors accept an
explicit ``units`` argument (``m`` by default for analytic primitives) and
normalise dimensions immediately.  A rigid transform maps local coordinates
to project coordinates and consists of a proper orthonormal rotation and a
translation in metres.

The primitive conventions are deliberately simple: boxes and spheres are
centred at their local origin; cylinders, cones, and frustums have their
base on ``z=0`` and extend to ``z=height``.  Meshes use indexed triangular
faces.  Mesh volume and inertia are available only as safe solid properties
when the mesh is closed and has consistent, non-degenerate topology.
"""

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .units import normalize_unit, to_si, unit_dimension


Vector3 = Tuple[float, float, float]
Matrix3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]
Tensor3 = Matrix3


def _finite(value, label="value"):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(label))
    return number


def _vector(value, label="vector"):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("{} must contain three numbers".format(label))
    return tuple(_finite(item, label) for item in value)


def _matrix(value, label="matrix"):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("{} must be a 3x3 matrix".format(label))
    rows = tuple(_vector(row, label) for row in value)
    return rows


def _identity_matrix():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _mat_vec(matrix, value):
    return tuple(sum(matrix[row][column] * value[column] for column in range(3)) for row in range(3))


def _mat_mul(first, second):
    return tuple(
        tuple(sum(first[row][k] * second[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _transpose(matrix):
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def _dot(first, second):
    return sum(first[index] * second[index] for index in range(3))


def _cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _add(first, second):
    return tuple(first[index] + second[index] for index in range(3))


def _sub(first, second):
    return tuple(first[index] - second[index] for index in range(3))


def _scale(value, factor):
    return tuple(factor * item for item in value)


def _symmetrize(matrix):
    return tuple(
        tuple((matrix[row][column] + matrix[column][row]) / 2.0 for column in range(3))
        for row in range(3)
    )


def _zero_tensor():
    return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def _rotate_tensor(rotation, tensor):
    return _symmetrize(_mat_mul(_mat_mul(rotation, tensor), _transpose(rotation)))


def _parallel_axis(mass, offset):
    squared = _dot(offset, offset)
    return tuple(
        tuple(
            mass * (squared if row == column else 0.0) - mass * offset[row] * offset[column]
            for column in range(3)
        )
        for row in range(3)
    )


def _tensor_add(first, second):
    return tuple(tuple(first[i][j] + second[i][j] for j in range(3)) for i in range(3))


def _tensor_scale(tensor, factor):
    return tuple(tuple(factor * tensor[i][j] for j in range(3)) for i in range(3))


def _length(value, units, label="length"):
    return to_si(_finite(value, label), normalize_unit(units), expected_dimension="length")


def _length_vector(value, units, label="length vector"):
    return tuple(_length(item, units, label) for item in _vector(value, label))


def _validate_positive(value, label):
    number = _finite(value, label)
    if number <= 0.0:
        raise ValueError("{} must be positive".format(label))
    return number


@dataclass(frozen=True)
class Transform:
    """A proper rigid transform ``world = rotation * local + translation``."""

    rotation: Matrix3 = _identity_matrix()
    translation: Vector3 = (0.0, 0.0, 0.0)

    def __post_init__(self):
        rotation = _matrix(self.rotation, "rotation")
        translation = _vector(self.translation, "translation")
        transpose = _transpose(rotation)
        product = _mat_mul(rotation, transpose)
        for row in range(3):
            for column in range(3):
                expected = 1.0 if row == column else 0.0
                if abs(product[row][column] - expected) > 1e-8:
                    raise ValueError("rotation must be orthonormal")
        determinant = _dot(rotation[0], _cross(rotation[1], rotation[2]))
        if abs(determinant - 1.0) > 1e-8:
            raise ValueError("rotation must be a proper rigid rotation")
        object.__setattr__(self, "rotation", rotation)
        object.__setattr__(self, "translation", translation)

    @classmethod
    def identity(cls):
        return cls()

    @classmethod
    def from_translation(cls, translation, units="m"):
        return cls(translation=_length_vector(translation, units, "translation"))

    @classmethod
    def from_matrix(cls, matrix, units="m"):
        values = tuple(tuple(float(item) for item in row) for row in matrix)
        if len(values) == 4 and all(len(row) == 4 for row in values):
            if any(abs(values[3][index] - (1.0 if index == 3 else 0.0)) > 1e-8 for index in range(4)):
                raise ValueError("homogeneous matrix must have a final [0, 0, 0, 1] row")
            return cls(tuple(row[:3] for row in values[:3]), _length_vector((values[0][3], values[1][3], values[2][3]), units))
        return cls(_matrix(values), (0.0, 0.0, 0.0))

    def apply_point(self, point):
        return _add(_mat_vec(self.rotation, _vector(point, "point")), self.translation)

    def apply_vector(self, vector):
        return _mat_vec(self.rotation, _vector(vector, "vector"))

    def compose(self, other):
        """Return ``self`` after ``other`` (``self(other(point))``)."""

        if not isinstance(other, Transform):
            raise TypeError("other must be a Transform")
        return Transform(
            _mat_mul(self.rotation, other.rotation),
            _add(_mat_vec(self.rotation, other.translation), self.translation),
        )

    def inverse(self):
        rotation = _transpose(self.rotation)
        return Transform(rotation, _scale(_mat_vec(rotation, self.translation), -1.0))

    def to_dict(self):
        return {"rotation": [list(row) for row in self.rotation], "translation": list(self.translation), "units": "m"}


RigidTransform = Transform


@dataclass(frozen=True)
class Bounds:
    """Axis-aligned bounds in metres."""

    min_point: Vector3
    max_point: Vector3

    def __post_init__(self):
        minimum = _vector(self.min_point, "min_point")
        maximum = _vector(self.max_point, "max_point")
        if any(minimum[i] > maximum[i] for i in range(3)):
            raise ValueError("bounds minimum cannot exceed maximum")
        object.__setattr__(self, "min_point", minimum)
        object.__setattr__(self, "max_point", maximum)

    @property
    def minimum(self):
        return self.min_point

    @property
    def maximum(self):
        return self.max_point

    @property
    def min(self):
        return self.min_point

    @property
    def max(self):
        return self.max_point

    @property
    def size(self):
        return _sub(self.max_point, self.min_point)

    @property
    def center(self):
        return _scale(_add(self.min_point, self.max_point), 0.5)

    def contains(self, point, tolerance=0.0):
        return all(self.min_point[i] - tolerance <= point[i] <= self.max_point[i] + tolerance for i in range(3))

    def union(self, other):
        if other is None:
            return self
        return Bounds(
            tuple(min(self.min_point[i], other.min_point[i]) for i in range(3)),
            tuple(max(self.max_point[i], other.max_point[i]) for i in range(3)),
        )

    @classmethod
    def from_points(cls, points):
        values = tuple(_vector(point, "point") for point in points)
        if not values:
            raise ValueError("at least one point is required")
        return cls(
            tuple(min(point[i] for point in values) for i in range(3)),
            tuple(max(point[i] for point in values) for i in range(3)),
        )

    def transformed(self, transform):
        return Bounds.from_points(transform.apply_point(corner) for corner in _box_corners(self.min_point, self.max_point))

    def to_dict(self):
        return {"min": list(self.min_point), "max": list(self.max_point), "units": "m"}


@dataclass(frozen=True)
class GeometricProperties:
    """Uniform-density geometric properties; mass uses the supplied density."""

    volume_m3: float
    surface_area_m2: float
    centroid_m: Optional[Vector3]
    inertia_tensor_unit_density: Optional[Tensor3]
    closed: bool = True
    diagnostics: Tuple[str, ...] = ()

    @property
    def centroid(self):
        return self.centroid_m

    @property
    def inertia_tensor(self):
        return self.inertia_tensor_unit_density

    def with_density(self, density_kg_m3):
        density = _validate_positive(density_kg_m3, "density")
        if self.centroid_m is None or self.inertia_tensor_unit_density is None:
            return None
        return {
            "mass_kg": density * self.volume_m3,
            "centroid_m": self.centroid_m,
            "inertia_tensor_kg_m2": _tensor_scale(self.inertia_tensor_unit_density, density),
        }


class Geometry:
    """Common protocol implemented by analytic solids, compounds, and meshes."""

    kind = "geometry"

    def aabb(self):
        return self.bounds()

    @property
    def volume_m3(self):
        return self.volume()

    @property
    def surface_area_m2(self):
        return self.surface_area()

    def properties(self, density=1.0):
        value = self.geometric_properties()
        if density == 1.0:
            return value
        weighted = value.with_density(density)
        if weighted is None:
            return value
        return weighted

    def mass_properties(self, density=1.0):
        """Return a small mass-property mapping at the requested density."""

        value = self.geometric_properties().with_density(density)
        return value


def _box_corners(minimum, maximum):
    return tuple(
        (minimum[0] if x == 0 else maximum[0], minimum[1] if y == 0 else maximum[1], minimum[2] if z == 0 else maximum[2])
        for x in range(2)
        for y in range(2)
        for z in range(2)
    )


def _transformed_local_bounds(minimum, maximum, transform):
    return Bounds.from_points(transform.apply_point(corner) for corner in _box_corners(minimum, maximum))


def _radial_bounds(transform, bottom_radius, top_radius, height):
    """Exact AABB for a linearly tapered body of revolution."""

    minimum = []
    maximum = []
    for row in range(3):
        radial_factor = math.sqrt(transform.rotation[row][0] ** 2 + transform.rotation[row][1] ** 2)
        axial_factor = transform.rotation[row][2]
        low_values = (-radial_factor * bottom_radius, -radial_factor * top_radius + axial_factor * height)
        high_values = (radial_factor * bottom_radius, radial_factor * top_radius + axial_factor * height)
        minimum.append(transform.translation[row] + min(low_values))
        maximum.append(transform.translation[row] + max(high_values))
    return Bounds(tuple(minimum), tuple(maximum))


def _transform_centroid_and_inertia(transform, centroid, tensor):
    return transform.apply_point(centroid), _rotate_tensor(transform.rotation, tensor)


@dataclass(frozen=True, init=False)
class Box(Geometry):
    """Rectangular solid with ``size=(x, y, z)``."""

    size: Vector3
    units: str
    transform: Transform
    kind = "box"

    def __init__(self, *args, size=None, units="m", transform=None, width=None, height=None, depth=None):
        if args:
            if len(args) == 1 and size is None:
                size = args[0]
            elif len(args) == 2 and size is None and isinstance(args[1], str):
                size, units = args
            elif len(args) in (3, 4) and size is None:
                size = args[:3]
                if len(args) == 4:
                    units = args[3]
            else:
                raise TypeError("Box expects size or width, height, depth")
        if size is None:
            if width is None or height is None or depth is None:
                raise TypeError("Box requires size or width, height, depth")
            size = (width, height, depth)
        canonical_units = normalize_unit(units)
        if unit_dimension(canonical_units) != "length":
            raise ValueError("Box units must be a length unit")
        dimensions = _length_vector(size, canonical_units, "Box size")
        if any(item <= 0.0 for item in dimensions):
            raise ValueError("Box dimensions must be positive")
        object.__setattr__(self, "size", dimensions)
        object.__setattr__(self, "units", "m")
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, canonical_units))

    def volume(self):
        return self.size[0] * self.size[1] * self.size[2]

    def signed_volume(self):
        return self.volume()

    def surface_area(self):
        x, y, z = self.size
        return 2.0 * (x * y + x * z + y * z)

    def centroid(self):
        return self.transform.apply_point((0.0, 0.0, 0.0))

    def bounds(self):
        half = _scale(self.size, 0.5)
        return _transformed_local_bounds(_scale(half, -1.0), half, self.transform)

    def inertia_tensor(self, density=1.0):
        x, y, z = self.size
        mass = _validate_positive(density, "density") * self.volume()
        local = (
            (mass * (y * y + z * z) / 12.0, 0.0, 0.0),
            (0.0, mass * (x * x + z * z) / 12.0, 0.0),
            (0.0, 0.0, mass * (x * x + y * y) / 12.0),
        )
        return _rotate_tensor(self.transform.rotation, local)

    def geometric_properties(self):
        unit_tensor = self.inertia_tensor(1.0)
        return GeometricProperties(self.volume(), self.surface_area(), self.centroid(), unit_tensor)

    def to_dict(self):
        return {"type": self.kind, "size": list(self.size), "units": "m", "transform": self.transform.to_dict()}


@dataclass(frozen=True, init=False)
class Sphere(Geometry):
    radius: float
    units: str
    transform: Transform
    kind = "sphere"

    def __init__(self, radius, units="m", transform=None):
        canonical_units = normalize_unit(units)
        if unit_dimension(canonical_units) != "length":
            raise ValueError("Sphere units must be a length unit")
        value = _length(radius, canonical_units, "Sphere radius")
        if value <= 0.0:
            raise ValueError("Sphere radius must be positive")
        object.__setattr__(self, "radius", value)
        object.__setattr__(self, "units", "m")
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, canonical_units))

    def volume(self):
        return 4.0 * math.pi * self.radius ** 3 / 3.0

    def signed_volume(self):
        return self.volume()

    def surface_area(self):
        return 4.0 * math.pi * self.radius ** 2

    def centroid(self):
        return self.transform.translation

    def bounds(self):
        minimum = tuple(self.transform.translation[i] - self.radius for i in range(3))
        maximum = tuple(self.transform.translation[i] + self.radius for i in range(3))
        return Bounds(minimum, maximum)

    def inertia_tensor(self, density=1.0):
        mass = _validate_positive(density, "density") * self.volume()
        value = 2.0 * mass * self.radius ** 2 / 5.0
        return ((value, 0.0, 0.0), (0.0, value, 0.0), (0.0, 0.0, value))

    def geometric_properties(self):
        return GeometricProperties(self.volume(), self.surface_area(), self.centroid(), self.inertia_tensor(1.0))

    def to_dict(self):
        return {"type": self.kind, "radius": self.radius, "units": "m", "transform": self.transform.to_dict()}


@dataclass(frozen=True, init=False)
class Cylinder(Geometry):
    radius: float
    height: float
    units: str
    transform: Transform
    kind = "cylinder"

    def __init__(self, radius, height, units="m", transform=None):
        canonical_units = normalize_unit(units)
        if unit_dimension(canonical_units) != "length":
            raise ValueError("Cylinder units must be a length unit")
        radius_si = _length(radius, canonical_units, "Cylinder radius")
        height_si = _length(height, canonical_units, "Cylinder height")
        if radius_si <= 0.0 or height_si <= 0.0:
            raise ValueError("Cylinder radius and height must be positive")
        object.__setattr__(self, "radius", radius_si)
        object.__setattr__(self, "height", height_si)
        object.__setattr__(self, "units", "m")
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, canonical_units))

    def volume(self):
        return math.pi * self.radius ** 2 * self.height

    def signed_volume(self):
        return self.volume()

    def surface_area(self):
        return 2.0 * math.pi * self.radius * (self.radius + self.height)

    def centroid(self):
        return self.transform.apply_point((0.0, 0.0, self.height / 2.0))

    def bounds(self):
        return _radial_bounds(self.transform, self.radius, self.radius, self.height)

    def inertia_tensor(self, density=1.0):
        mass = _validate_positive(density, "density") * self.volume()
        transverse = mass * (3.0 * self.radius ** 2 + self.height ** 2) / 12.0
        axial = mass * self.radius ** 2 / 2.0
        local = ((transverse, 0.0, 0.0), (0.0, transverse, 0.0), (0.0, 0.0, axial))
        return _rotate_tensor(self.transform.rotation, local)

    def geometric_properties(self):
        return GeometricProperties(self.volume(), self.surface_area(), self.centroid(), self.inertia_tensor(1.0))

    def to_dict(self):
        return {"type": self.kind, "radius": self.radius, "height": self.height, "units": "m", "transform": self.transform.to_dict()}


@dataclass(frozen=True, init=False)
class Cone(Geometry):
    base_radius: float
    height: float
    units: str
    transform: Transform
    kind = "cone"

    def __init__(self, base_radius=None, height=None, units="m", transform=None, radius=None):
        if base_radius is None:
            base_radius = radius
        if base_radius is None or height is None:
            raise TypeError("Cone requires base_radius and height")
        canonical_units = normalize_unit(units)
        if unit_dimension(canonical_units) != "length":
            raise ValueError("Cone units must be a length unit")
        radius_si = _length(base_radius, canonical_units, "Cone base_radius")
        height_si = _length(height, canonical_units, "Cone height")
        if radius_si <= 0.0 or height_si <= 0.0:
            raise ValueError("Cone base_radius and height must be positive")
        object.__setattr__(self, "base_radius", radius_si)
        object.__setattr__(self, "height", height_si)
        object.__setattr__(self, "units", "m")
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, canonical_units))

    @property
    def radius(self):
        return self.base_radius

    def volume(self):
        return math.pi * self.base_radius ** 2 * self.height / 3.0

    def signed_volume(self):
        return self.volume()

    def surface_area(self):
        slant = math.sqrt(self.base_radius ** 2 + self.height ** 2)
        return math.pi * self.base_radius * (self.base_radius + slant)

    def centroid(self):
        return self.transform.apply_point((0.0, 0.0, self.height / 4.0))

    def bounds(self):
        return _radial_bounds(self.transform, self.base_radius, 0.0, self.height)

    def inertia_tensor(self, density=1.0):
        mass = _validate_positive(density, "density") * self.volume()
        axial = 3.0 * mass * self.base_radius ** 2 / 10.0
        transverse = 3.0 * mass * (4.0 * self.base_radius ** 2 + self.height ** 2) / 80.0
        local = ((transverse, 0.0, 0.0), (0.0, transverse, 0.0), (0.0, 0.0, axial))
        return _rotate_tensor(self.transform.rotation, local)

    def geometric_properties(self):
        return GeometricProperties(self.volume(), self.surface_area(), self.centroid(), self.inertia_tensor(1.0))

    def to_dict(self):
        return {"type": self.kind, "base_radius": self.base_radius, "height": self.height, "units": "m", "transform": self.transform.to_dict()}


@dataclass(frozen=True, init=False)
class Frustum(Geometry):
    bottom_radius: float
    top_radius: float
    height: float
    units: str
    transform: Transform
    kind = "frustum"

    def __init__(self, bottom_radius=None, top_radius=None, height=None, units="m", transform=None, r1=None, r2=None):
        if bottom_radius is None:
            bottom_radius = r1
        if top_radius is None:
            top_radius = r2
        if bottom_radius is None or top_radius is None or height is None:
            raise TypeError("Frustum requires bottom_radius, top_radius, and height")
        canonical_units = normalize_unit(units)
        if unit_dimension(canonical_units) != "length":
            raise ValueError("Frustum units must be a length unit")
        first = _length(bottom_radius, canonical_units, "Frustum bottom_radius")
        second = _length(top_radius, canonical_units, "Frustum top_radius")
        height_si = _length(height, canonical_units, "Frustum height")
        if first < 0.0 or second < 0.0 or height_si <= 0.0 or first + second <= 0.0:
            raise ValueError("Frustum radii must be non-negative and not both zero")
        object.__setattr__(self, "bottom_radius", first)
        object.__setattr__(self, "top_radius", second)
        object.__setattr__(self, "height", height_si)
        object.__setattr__(self, "units", "m")
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, canonical_units))

    def volume(self):
        first, second = self.bottom_radius, self.top_radius
        return math.pi * self.height * (first ** 2 + first * second + second ** 2) / 3.0

    def signed_volume(self):
        return self.volume()

    def surface_area(self):
        slant = math.sqrt((self.bottom_radius - self.top_radius) ** 2 + self.height ** 2)
        return math.pi * (self.bottom_radius + self.top_radius) * slant + math.pi * (self.bottom_radius ** 2 + self.top_radius ** 2)

    def _centroid_z(self):
        first, second = self.bottom_radius, self.top_radius
        denominator = first ** 2 + first * second + second ** 2
        return self.height * (first ** 2 + 2.0 * first * second + 3.0 * second ** 2) / (4.0 * denominator)

    def centroid(self):
        return self.transform.apply_point((0.0, 0.0, self._centroid_z()))

    def bounds(self):
        radius = max(self.bottom_radius, self.top_radius)
        return _radial_bounds(self.transform, self.bottom_radius, self.top_radius, self.height)

    def _integral(self, power, z_power=0):
        """Integrate ``z**z_power * radius(z)**power`` from 0 to height."""

        first, difference = self.bottom_radius, self.top_radius - self.bottom_radius
        total = 0.0
        for index in range(power + 1):
            coefficient = math.comb(power, index) * first ** (power - index) * difference ** index
            total += coefficient * self.height ** (z_power + 1) / float(index + z_power + 1)
        return total

    def inertia_tensor(self, density=1.0):
        density = _validate_positive(density, "density")
        volume = self.volume()
        mass = density * volume
        first_moment = math.pi * self._integral(2, 1)
        centroid_z = first_moment / volume
        second_z = math.pi * self._integral(2, 2)
        variance_z = second_z - volume * centroid_z ** 2
        radial_second = math.pi * self._integral(4) / 4.0
        transverse = density * (radial_second + variance_z)
        axial = density * math.pi * self._integral(4) / 2.0
        local = ((transverse, 0.0, 0.0), (0.0, transverse, 0.0), (0.0, 0.0, axial))
        return _rotate_tensor(self.transform.rotation, local)

    def geometric_properties(self):
        return GeometricProperties(self.volume(), self.surface_area(), self.centroid(), self.inertia_tensor(1.0))

    def to_dict(self):
        return {"type": self.kind, "bottom_radius": self.bottom_radius, "top_radius": self.top_radius, "height": self.height, "units": "m", "transform": self.transform.to_dict()}


@dataclass(frozen=True)
class MeshDiagnostics:
    """Topology and signed-solid diagnostics for a triangle mesh."""

    closed: bool
    boundary_edges: int
    nonmanifold_edges: int
    degenerate_triangles: int
    inconsistent_winding: bool
    signed_volume_m3: float
    centroid_m: Optional[Vector3]
    safe_for_mass_properties: bool
    issues: Tuple[str, ...] = ()

    @property
    def volume_m3(self):
        return abs(self.signed_volume_m3)

    def to_dict(self):
        return {
            "closed": self.closed,
            "boundary_edges": self.boundary_edges,
            "nonmanifold_edges": self.nonmanifold_edges,
            "degenerate_triangles": self.degenerate_triangles,
            "inconsistent_winding": self.inconsistent_winding,
            "signed_volume_m3": self.signed_volume_m3,
            "centroid_m": list(self.centroid_m) if self.centroid_m is not None else None,
            "safe_for_mass_properties": self.safe_for_mass_properties,
            "issues": list(self.issues),
        }


def _triangle_area(first, second, third):
    return math.sqrt(_dot(_cross(_sub(second, first), _sub(third, first)), _cross(_sub(second, first), _sub(third, first)))) / 2.0


def _mesh_integrals(vertices, triangles):
    volume = 0.0
    first = (0.0, 0.0, 0.0)
    second = _zero_tensor()
    for i, j, k in triangles:
        a, b, c = vertices[i], vertices[j], vertices[k]
        tetra_volume = _dot(a, _cross(b, c)) / 6.0
        volume += tetra_volume
        first = _add(first, _scale(_add(_add(a, b), c), tetra_volume / 4.0))
        vertices_for_moment = (a, b, c)
        tetra_second = _zero_tensor()
        for left in vertices_for_moment:
            tetra_second = _tensor_add(tetra_second, _tensor_scale(_outer(left, left), 2.0))
        for left_index in range(3):
            for right_index in range(left_index + 1, 3):
                left, right = vertices_for_moment[left_index], vertices_for_moment[right_index]
                tetra_second = _tensor_add(tetra_second, _tensor_add(_outer(left, right), _outer(right, left)))
        second = _tensor_add(second, _tensor_scale(tetra_second, tetra_volume / 20.0))
    return volume, first, second


def _outer(first, second):
    return tuple(tuple(first[row] * second[column] for column in range(3)) for row in range(3))


def _mesh_inertia_from_integrals(volume, first, second, density=1.0):
    if abs(volume) <= 1e-15:
        raise ValueError("mesh has zero signed volume")
    sign = 1.0 if volume > 0.0 else -1.0
    positive_volume = sign * volume
    positive_first = _scale(first, sign)
    positive_second = _tensor_scale(second, sign)
    centroid = _scale(positive_first, 1.0 / positive_volume)
    unit_origin = _tensor_scale(
        tuple(tuple((sum(positive_second[i][i] for i in range(3)) if i == j else 0.0) - positive_second[i][j] for j in range(3)) for i in range(3)),
        1.0,
    )
    unit_centroid = _tensor_add(unit_origin, _tensor_scale(_parallel_axis(positive_volume, centroid), -1.0))
    return centroid, _tensor_scale(unit_centroid, density)


@dataclass(frozen=True, init=False)
class TriangleMesh(Geometry):
    """Indexed triangle mesh with vertices normalised to metres."""

    vertices: Tuple[Vector3, ...]
    triangles: Tuple[Tuple[int, int, int], ...]
    units: str
    transform: Transform
    kind = "mesh"

    def __init__(self, vertices, triangles=None, units="m", transform=None, faces=None):
        if triangles is None:
            triangles = faces
        if triangles is None:
            raise TypeError("TriangleMesh requires triangles")
        canonical_units = normalize_unit(units)
        if unit_dimension(canonical_units) != "length":
            raise ValueError("TriangleMesh units must be a length unit")
        normalized_vertices = tuple(_length_vector(vertex, canonical_units, "mesh vertex") for vertex in vertices)
        normalized_triangles = []
        for triangle in triangles:
            if not isinstance(triangle, (list, tuple)) or len(triangle) != 3:
                raise ValueError("mesh triangles must contain three indices")
            values = tuple(int(index) for index in triangle)
            if any(index < 0 or index >= len(normalized_vertices) for index in values):
                raise ValueError("mesh triangle index out of range")
            normalized_triangles.append(values)
        if not normalized_vertices or not normalized_triangles:
            raise ValueError("mesh requires vertices and triangles")
        object.__setattr__(self, "vertices", normalized_vertices)
        object.__setattr__(self, "triangles", tuple(normalized_triangles))
        object.__setattr__(self, "units", "m")
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, canonical_units))

    @property
    def faces(self):
        return self.triangles

    def _world_vertices(self):
        return tuple(self.transform.apply_point(vertex) for vertex in self.vertices)

    def _integrals(self):
        """Volume integrals, computed once per mesh instance.

        The pipeline runs diagnostics, classification, mass, and validation
        over the same meshes; caching removes repeated O(V) integral passes.
        """
        cached = getattr(self, "_integrals_cache", None)
        if cached is None:
            cached = _mesh_integrals(self._world_vertices(), self.triangles)
            object.__setattr__(self, "_integrals_cache", cached)
        return cached

    def bounds(self):
        return Bounds.from_points(self._world_vertices())

    def surface_area(self):
        vertices = self._world_vertices()
        return sum(_triangle_area(vertices[i], vertices[j], vertices[k]) for i, j, k in self.triangles)

    def _topology(self):
        edges = {}
        degenerate = 0
        vertices = self._world_vertices()
        for triangle in self.triangles:
            i, j, k = triangle
            if _triangle_area(vertices[i], vertices[j], vertices[k]) <= 1e-15:
                degenerate += 1
            for first, second in ((i, j), (j, k), (k, i)):
                key = tuple(sorted((first, second)))
                direction = 1 if (first, second) == key else -1
                edges.setdefault(key, []).append(direction)
        boundary = sum(1 for values in edges.values() if len(values) == 1)
        nonmanifold = sum(1 for values in edges.values() if len(values) > 2)
        inconsistent = any(len(values) == 2 and values[0] == values[1] for values in edges.values())
        return boundary, nonmanifold, degenerate, inconsistent

    def diagnostics(self):
        boundary, nonmanifold, degenerate, inconsistent = self._topology()
        volume, first, _ = self._integrals()
        centroid = _scale(first, 1.0 / volume) if abs(volume) > 1e-15 else None
        closed = boundary == 0 and nonmanifold == 0 and bool(self.triangles)
        issues = []
        if boundary:
            issues.append("boundary_edges")
        if nonmanifold:
            issues.append("nonmanifold_edges")
        if degenerate:
            issues.append("degenerate_triangles")
        if inconsistent:
            issues.append("inconsistent_winding")
        if abs(volume) <= 1e-15:
            issues.append("zero_signed_volume")
        safe = closed and degenerate == 0 and not inconsistent and abs(volume) > 1e-15
        return MeshDiagnostics(closed, boundary, nonmanifold, degenerate, inconsistent, volume, centroid, safe, tuple(issues))

    def closed_mesh_diagnostics(self):
        return self.diagnostics()

    def signed_volume(self):
        return self.diagnostics().signed_volume_m3

    def volume(self):
        return abs(self.signed_volume())

    def centroid(self):
        return self.diagnostics().centroid_m

    def inertia_tensor(self, density=1.0):
        diagnostics = self.diagnostics()
        if not diagnostics.safe_for_mass_properties:
            raise ValueError("mesh is not safe for mass properties: {}".format(", ".join(diagnostics.issues)))
        volume, first, second = self._integrals()
        _, tensor = _mesh_inertia_from_integrals(volume, first, second, _validate_positive(density, "density"))
        return tensor

    def geometric_properties(self):
        diagnostics = self.diagnostics()
        tensor = self.inertia_tensor(1.0) if diagnostics.safe_for_mass_properties else None
        return GeometricProperties(self.volume(), self.surface_area(), diagnostics.centroid_m, tensor, diagnostics.closed, diagnostics.issues)

    def to_dict(self):
        return {"type": self.kind, "vertices": [list(vertex) for vertex in self.vertices], "triangles": [list(triangle) for triangle in self.triangles], "units": "m", "transform": self.transform.to_dict()}


@dataclass(frozen=True, init=False)
class Compound(Geometry):
    """A deterministic collection of bodies; overlaps are not boolean-unioned."""

    children: Tuple[Geometry, ...]
    transform: Transform
    kind = "compound"

    def __init__(self, children, transform=None):
        values = tuple(children)
        if not values or any(not isinstance(child, Geometry) for child in values):
            raise ValueError("Compound requires one or more Geometry children")
        object.__setattr__(self, "children", values)
        object.__setattr__(self, "transform", transform if isinstance(transform, Transform) else Transform.identity() if transform is None else _coerce_transform(transform, "m"))

    def _properties(self):
        values = []
        for child in self.children:
            value = child.geometric_properties()
            centroid, tensor = _transform_centroid_and_inertia(self.transform, value.centroid_m, value.inertia_tensor_unit_density) if value.centroid_m is not None and value.inertia_tensor_unit_density is not None else (None, None)
            values.append((value, centroid, tensor))
        total_volume = sum(value.volume_m3 for value, _, _ in values)
        if total_volume <= 0.0:
            return total_volume, None, None
        if any(center is None or local_tensor is None for _, center, local_tensor in values):
            return total_volume, None, None
        centroid = _scale(tuple(sum(value.volume_m3 * center[i] for value, center, _ in values if center is not None) for i in range(3)), 1.0 / total_volume)
        tensor = _zero_tensor()
        for value, center, local_tensor in values:
            if center is None or local_tensor is None:
                continue
            tensor = _tensor_add(tensor, local_tensor)
            tensor = _tensor_add(tensor, _parallel_axis(value.volume_m3, _sub(center, centroid)))
        return total_volume, centroid, tensor

    def volume(self):
        return sum(child.volume() for child in self.children)

    def signed_volume(self):
        return sum(child.signed_volume() for child in self.children)

    def surface_area(self):
        return sum(child.surface_area() for child in self.children)

    def centroid(self):
        return self._properties()[1]

    def bounds(self):
        result = None
        for child in self.children:
            value = child.bounds().transformed(self.transform)
            result = value if result is None else result.union(value)
        return result

    def inertia_tensor(self, density=1.0):
        value = self._properties()
        if value[2] is None:
            raise ValueError("compound has no positive-volume children with mass properties")
        return _tensor_scale(value[2], _validate_positive(density, "density"))

    def geometric_properties(self):
        volume, centroid, tensor = self._properties()
        return GeometricProperties(volume, self.surface_area(), centroid, tensor, all(child.geometric_properties().closed for child in self.children))

    def to_dict(self):
        return {"type": self.kind, "children": [child.to_dict() for child in self.children], "transform": self.transform.to_dict()}


def _coerce_transform(value, units):
    if isinstance(value, Transform):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("transform must be a Transform or object")
    rotation = value.get("rotation", value.get("basis", _identity_matrix()))
    translation = value.get("translation", value.get("origin", (0.0, 0.0, 0.0)))
    transform_units = value.get("units", units)
    return Transform(_matrix(rotation), _length_vector(translation, transform_units, "translation"))


def geometry_from_dict(data, units=None):
    """Build a geometry from analytic or mesh JSON-compatible data."""

    if not isinstance(data, Mapping):
        raise ValueError("geometry definition must be an object")
    if "geometry" in data and isinstance(data["geometry"], Mapping):
        data = data["geometry"]
    kind = str(data.get("type", data.get("kind", data.get("geometry_type", data.get("shape", data.get("primitive", "")))))).lower()
    source_units = data.get("units", units or "m")
    transform = _coerce_transform(data.get("transform", {}), source_units)
    if kind == "box":
        size = data.get("size", data.get("dimensions"))
        if size is None:
            size = (data.get("width"), data.get("height"), data.get("depth"))
        return Box(size=size, units=source_units, transform=transform)
    if kind == "sphere":
        return Sphere(data["radius"], units=source_units, transform=transform)
    if kind == "cylinder":
        return Cylinder(data["radius"], data["height"], units=source_units, transform=transform)
    if kind == "cone":
        return Cone(data.get("base_radius", data.get("radius")), data["height"], units=source_units, transform=transform)
    if kind == "frustum":
        return Frustum(data.get("bottom_radius", data.get("r1")), data.get("top_radius", data.get("r2")), data["height"], units=source_units, transform=transform)
    if kind in ("mesh", "triangle_mesh", "stl", "obj"):
        return TriangleMesh(data["vertices"], data.get("triangles", data.get("faces", data.get("indices"))), units=source_units, transform=transform)
    if kind == "compound":
        children = tuple(geometry_from_dict(child, units=source_units) for child in data.get("children", data.get("geometries", ())))
        return Compound(children, transform=transform)
    raise ValueError("unsupported geometry type: {!r}".format(kind))


def closed_mesh_diagnostics(mesh):
    """Return structured diagnostics for a :class:`TriangleMesh`."""

    if not isinstance(mesh, TriangleMesh):
        raise TypeError("closed_mesh_diagnostics expects a TriangleMesh")
    return mesh.diagnostics()


AABB = Bounds
Mesh = TriangleMesh


__all__ = [
    "Vector3",
    "Matrix3",
    "Tensor3",
    "Transform",
    "RigidTransform",
    "Bounds",
    "AABB",
    "GeometricProperties",
    "Geometry",
    "Box",
    "Sphere",
    "Cylinder",
    "Cone",
    "Frustum",
    "TriangleMesh",
    "Mesh",
    "Compound",
    "MeshDiagnostics",
    "closed_mesh_diagnostics",
    "geometry_from_dict",
]
