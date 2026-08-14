"""Independent analytic verification of the shell pipeline physics quantities.

Validation-preparation phase, item 13: before the drop model is compared
against real measurements, its computed physics quantities are checked
against independent closed-form results.  Every expected value below is
computed from the analytic formula written in the test itself — the
implementation is never used to derive the expectation.  The implementation
is only *set up* (geometry/mesh construction, material densities) and its
reported values are then compared against the closed forms.

Formulas covered (each stated in the test docstring):

1.  Mesh volume/mass:   V_box = a*b*c (exact for a box mesh), m = rho*V;
                        V_sphere = 4/3*pi*r^3 (tessellation-approximate).
2.  Center of mass:     x_c = (m1*x1 + m2*x2)/(m1 + m2) (composite),
                        signed-volume integration of a union mesh.
3.  Inertia:            I_cube = m*a^2/6; I_xx = m*(b^2 + c^2)/12;
                        parallel-axis theorem for the composite.
4.  Drop energy:        E_release = m*g*(h - z_lowest) with g = 9.81
                        (drop_sim.GRAVITY_M_S2, drop_sim.py:635).
5.  Impact speed:       v = sqrt(2*g*h).
6.  Contact impulse:    J = m*v; linear spring F_peak = v*sqrt(k*m);
                        compression duration t = (pi/2)*sqrt(m/k)
                        (impact.py linear branch, see docstrings below).
7.  Beams/plate:        w = F*L^3/(3*E*I); w = 5*q*L^4/(384*E*I);
                        w_1 = 16*p/(D*pi^6*(1/a^2 + 1/b^2)^2) (Navier order 1);
                        located point loads (Roark): SS w(x0) =
                        P*x0^2*(L-x0)^2/(3*E*I*L); cantilever w(a) = P*a^3/(3*E*I).
8.  Stress:             sigma = M*c/I; sigma = beta*p*a^2/t^2,
                        beta = 0.2874; 100-term Navier double sum over the
                        interior 3x3 grid; tau = 1.5*V/A (max_shear_pa).
9.  Units:              1 N = 1 kg*m/s^2; g0 = 9.80665 vs 9.81 (documented).
"""

import math
import unittest

from mouse_sim import TriangleMesh, mass_properties
from mouse_sim.drop_sim import GRAVITY_M_S2, box_inertia, simulate, support_points
from mouse_sim.impact import estimate_impact
from mouse_sim.physics import beam_response, shell_panel_response
from mouse_sim.units import UNIT_SPECS, to_si

G_STANDARD_M_S2 = 9.80665  # documented standard gravity (units "g0")
G_DROP_M_S2 = 9.81  # drop_sim integrator convention (documented)


def box_mesh(size, center=(0.0, 0.0, 0.0)):
    """Axis-aligned box TriangleMesh with its centroid at ``center``."""
    sx, sy, sz = size
    cx, cy, cz = center
    x0, x1 = cx - sx / 2.0, cx + sx / 2.0
    y0, y1 = cy - sy / 2.0, cy + sy / 2.0
    z0, z1 = cz - sz / 2.0, cz + sz / 2.0
    vertices = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    triangles = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]
    return TriangleMesh(vertices, triangles)


def uv_sphere_mesh(radius, rings=24, sectors=48):
    """UV-sphere TriangleMesh: latitude rings plus longitude sectors.

    All vertices lie exactly on the sphere surface, so the polyhedron is
    inscribed and its signed-volume integral converges to 4/3*pi*r^3 from
    below.  Winding is outward (verified: the signed volume is positive).
    """
    vertices = [(0.0, 0.0, radius), (0.0, 0.0, -radius)]
    for lat in range(1, rings):
        phi = math.pi * lat / rings
        sp = math.sin(phi)
        for lon in range(sectors):
            theta = 2.0 * math.pi * lon / sectors
            vertices.append(
                (radius * sp * math.cos(theta), radius * sp * math.sin(theta),
                 radius * math.cos(phi))
            )

    def index(lat, lon):
        return 2 + (lat - 1) * sectors + (lon % sectors)

    triangles = []
    for lon in range(sectors):
        triangles.append((0, index(1, lon), index(1, lon + 1)))
        triangles.append((1, index(rings - 1, lon + 1), index(rings - 1, lon)))
    for lat in range(1, rings - 1):
        for lon in range(sectors):
            triangles.append((index(lat, lon), index(lat + 1, lon), index(lat + 1, lon + 1)))
            triangles.append((index(lat, lon), index(lat + 1, lon + 1), index(lat, lon + 1)))
    return TriangleMesh(vertices, triangles)


def union_mesh(meshes):
    """One TriangleMesh whose surface is the union of closed meshes."""
    vertices = []
    triangles = []
    for mesh in meshes:
        offset = len(vertices)
        vertices.extend(mesh.vertices)
        triangles.extend(
            (i + offset, j + offset, k + offset) for i, j, k in mesh.triangles
        )
    return TriangleMesh(vertices, triangles)


class MeshMassAndVolumeTests(unittest.TestCase):
    """Item 1: mass vs analytically known geometry."""

    def test_box_mesh_volume_and_mass_exact(self):
        """V = a*b*c exactly; m = rho*V for rho = 2700 (Al) and 1040 (ABS).

        The mesh is a closed triangulated box; the divergence-theorem signed
        volume of an axis-aligned box is exact in floating point, so the
        mass formula is exact rather than approximate.
        """
        size = (0.06, 0.04, 0.03)
        mesh = box_mesh(size)
        volume_exact = 0.06 * 0.04 * 0.03
        for rho in (2700.0, 1040.0):
            result = mass_properties({"case": mesh}, {"case": rho})
            item = result.objects[0]
            self.assertEqual(item.mass_status, "calculated")
            self.assertAlmostEqual(item.volume_m3, volume_exact, places=12)
            self.assertAlmostEqual(item.mass_kg, rho * volume_exact, places=9)

    def test_sphere_mesh_volume_within_one_percent(self):
        """V = 4/3*pi*r^3 within 1% at moderate tessellation.

        The test-built UV sphere (24 rings x 48 sectors) is inscribed in the
        exact sphere, so its volume is below 4/3*pi*r^3 by 0.7% — inside the
        1% bound.  rho = 1040 (ABS): m = rho*4/3*pi*r^3.
        """
        radius = 0.05
        mesh = uv_sphere_mesh(radius)
        diagnostics = mesh.diagnostics()
        self.assertTrue(diagnostics.closed)
        self.assertTrue(diagnostics.safe_for_mass_properties)
        volume_exact = 4.0 / 3.0 * math.pi * radius ** 3
        self.assertLessEqual(
            abs(mesh.volume() - volume_exact) / volume_exact, 0.01
        )
        result = mass_properties({"sphere": mesh}, {"sphere": 1040.0})
        item = result.objects[0]
        self.assertAlmostEqual(item.mass_kg, 1040.0 * volume_exact, delta=0.01 * 1040.0 * volume_exact)


class CenterOfMassTests(unittest.TestCase):
    """Item 2: CoM vs asymmetric bodies."""

    def test_two_box_composite_com_mass_weighted(self):
        """x_c = (m1*x1 + m2*x2)/(m1 + m2), m_i = rho_i*V_i exactly.

        Two 0.1 m cubes offset in x with densities 2700 (Al) and 1040 (ABS);
        the aggregation formula is written here independently of mass.py.
        """
        size = (0.1, 0.1, 0.1)
        x1, x2 = 0.0, 0.1
        rho1, rho2 = 2700.0, 1040.0
        volume = 0.1 ** 3
        m1, m2 = rho1 * volume, rho2 * volume
        expected_x = (m1 * x1 + m2 * x2) / (m1 + m2)
        result = mass_properties(
            {
                "left": box_mesh(size, (x1, 0.0, 0.0)),
                "right": box_mesh(size, (x2, 0.0, 0.0)),
            },
            {"left": rho1, "right": rho2},
        )
        self.assertAlmostEqual(result.mass_kg, m1 + m2, places=9)
        self.assertAlmostEqual(result.center_of_mass_m[0], expected_x, places=9)
        # y/z stay zero for the x-offset assembly.
        self.assertAlmostEqual(result.center_of_mass_m[1], 0.0, places=12)
        self.assertAlmostEqual(result.center_of_mass_m[2], 0.0, places=12)

    def test_two_box_union_mesh_centroid_exact(self):
        """Signed-volume integration of a union mesh is exact for boxes.

        Two 0.1 m cubes sharing a face at x = 0.05, one mesh: the geometry
        centroid is the volume-weighted decomposition
        x_c = (V1*x1 + V2*x2)/(V1 + V2) = 0.05 exactly (flat faces, no
        tessellation error).
        """
        size = (0.1, 0.1, 0.1)
        mesh = union_mesh([box_mesh(size, (0.0, 0.0, 0.0)), box_mesh(size, (0.1, 0.0, 0.0))])
        diagnostics = mesh.diagnostics()
        self.assertTrue(diagnostics.closed)
        self.assertTrue(diagnostics.safe_for_mass_properties)
        v1 = v2 = 0.1 ** 3
        expected_x = (v1 * 0.0 + v2 * 0.1) / (v1 + v2)
        self.assertAlmostEqual(mesh.volume(), v1 + v2, places=12)
        self.assertAlmostEqual(mesh.centroid()[0], expected_x, places=12)
        # Mass path: uniform density keeps the volume-weighted centroid.
        result = mass_properties({"assembly": mesh}, {"assembly": 2700.0})
        self.assertAlmostEqual(result.center_of_mass_m[0], expected_x, places=9)

    def test_l_shape_com_from_decomposition(self):
        """L-prism CoM from box decomposition, within 0.5%.

        Two 0.1 m cubes sharing a face at y = 0.05 form an L prism; the
        analytic decomposition is x_c = y_c = 0, z_c = 0
        (x_c = (V1*x1 + V2*x2)/(V1 + V2), y_c = (V1*y1 + V2*y2)/(V1 + V2)).
        """
        size = (0.1, 0.1, 0.1)
        mesh = union_mesh([box_mesh(size, (0.0, 0.0, 0.0)), box_mesh(size, (0.0, 0.1, 0.0))])
        self.assertTrue(mesh.diagnostics().safe_for_mass_properties)
        v1 = v2 = 0.1 ** 3
        expected_x = (v1 * 0.0 + v2 * 0.0) / (v1 + v2)
        expected_y = (v1 * 0.0 + v2 * 0.1) / (v1 + v2)
        result = mass_properties({"L": mesh}, {"L": 1040.0})
        center = result.center_of_mass_m
        self.assertAlmostEqual(center[0], expected_x, delta=0.005 * 0.1)
        self.assertAlmostEqual(center[1], expected_y, delta=0.005 * 0.1)
        self.assertAlmostEqual(center[2], 0.0, delta=0.005 * 0.1)


class InertiaTests(unittest.TestCase):
    """Item 3: inertia from analytic closed forms."""

    def test_cube_mesh_inertia_about_centroidal_axes(self):
        """I = m*a^2/6 about any centroidal axis, within 0.1%.

        Cube a = 0.1 m, rho = 2700: m = 2700*a^3, I = m*a^2/6 = 4.5e-3 kg*m^2.
        The mesh integral is exact for a box; the 0.1% tolerance documents
        the integration method rather than limiting it.
        """
        a = 0.1
        rho = 2700.0
        mesh = box_mesh((a, a, a))
        result = mass_properties({"cube": mesh}, {"cube": rho})
        item = result.objects[0]
        m = rho * a ** 3
        expected = m * a ** 2 / 6.0
        for axis in range(3):
            self.assertAlmostEqual(
                item.inertia_tensor_kg_m2[axis][axis], expected,
                delta=0.001 * expected,
            )
        # About a corner axis, I = m*a^2/6 + m*d^2 (parallel axis) must
        # follow from the reported centroidal tensor and the analytic d.
        d2 = 3.0 * (a / 2.0) ** 2
        self.assertAlmostEqual(
            item.inertia_tensor_kg_m2[0][0] + m * d2, m * a ** 2 / 6.0 + m * d2,
            delta=0.001 * (m * a ** 2 / 6.0),
        )

    def test_rectangular_box_inertia(self):
        """I_xx = m*(b^2 + c^2)/12, within 0.1%.

        Box a x b x c = 0.08 x 0.05 x 0.04, rho = 1040:
        m = 1040*a*b*c, I_xx = m*(b^2 + c^2)/12.
        """
        a, b, c = 0.08, 0.05, 0.04
        rho = 1040.0
        mesh = box_mesh((a, b, c))
        result = mass_properties({"box": mesh}, {"box": rho})
        item = result.objects[0]
        m = rho * a * b * c
        self.assertAlmostEqual(item.mass_kg, m, places=9)
        self.assertAlmostEqual(
            item.inertia_tensor_kg_m2[0][0], m * (b ** 2 + c ** 2) / 12.0,
            delta=0.001 * m * (b ** 2 + c ** 2) / 12.0,
        )
        self.assertAlmostEqual(
            item.inertia_tensor_kg_m2[1][1], m * (a ** 2 + c ** 2) / 12.0,
            delta=0.001 * m * (a ** 2 + c ** 2) / 12.0,
        )

    def test_two_box_composite_inertia_parallel_axis(self):
        """Composite inertia about the combined CoM via parallel axis, 1%.

        Two 0.1 m cubes offset in x, rho1 = 2700, rho2 = 1040, CoM at
        x_c = (m1*x1 + m2*x2)/(m1 + m2).  For the z axis:
        I_zz = sum_i [m_i*(a_i^2 + b_i^2)/12 + m_i*(x_i - x_c)^2].
        """
        a = 0.1
        x1, x2 = 0.0, 0.1
        rho1, rho2 = 2700.0, 1040.0
        v = a ** 3
        m1, m2 = rho1 * v, rho2 * v
        x_c = (m1 * x1 + m2 * x2) / (m1 + m2)
        expected_zz = (
            m1 * (a ** 2 + a ** 2) / 12.0 + m1 * (x1 - x_c) ** 2
            + m2 * (a ** 2 + a ** 2) / 12.0 + m2 * (x2 - x_c) ** 2
        )
        result = mass_properties(
            {
                "left": box_mesh((a, a, a), (x1, 0.0, 0.0)),
                "right": box_mesh((a, a, a), (x2, 0.0, 0.0)),
            },
            {"left": rho1, "right": rho2},
        )
        self.assertAlmostEqual(
            result.inertia_tensor_kg_m2[2][2], expected_zz,
            delta=0.01 * expected_zz,
        )


class DropEnergyAndKinematicsTests(unittest.TestCase):
    """Items 4 and 5: drop energy vs m*g*h and impact speed vs sqrt(2gh)."""

    CUBE_SUPPORT = support_points(
        [(x, y, z) for x in (-0.05, 0.05) for y in (-0.05, 0.05) for z in (-0.05, 0.05)]
    )
    CUBE_INERTIA = box_inertia(0.1, ((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05)))

    def test_drop_release_energy_matches_mgh(self):
        """E_release = m*g*(h - z_lowest) with g = 9.81 (drop_sim convention).

        drop_sim.py:635 defines the release energy as
        mass*g*(height - lowest_world) + spin_budget (spin 0 for a drop).
        For the flat cube the lowest world support point is z = -0.05, so
        E = m*g*(h + 0.05); the recorded value must match exactly (the
        output is rounded to 6 decimals).
        """
        height = 0.5
        m = 0.1
        result = simulate(m, self.CUBE_INERTIA, self.CUBE_SUPPORT, height,
                          surface="concrete", drop_count=1, test="drop",
                          orientation="flat")
        self.assertEqual(result["drops"][0]["checks"], [])
        release = m * G_DROP_M_S2 * (height - (-0.05))
        self.assertAlmostEqual(result["drops"][0]["energy"]["release_j"], release, places=5)
        self.assertLessEqual(
            abs(result["drops"][0]["energy"]["release_j"] - release) / release, 0.001
        )

    def test_first_impact_speed_matches_sqrt_2gh(self):
        """v = sqrt(2*g*h) at h = 0.5, flat concrete, within 1%.

        The CoM starts h above the lowest support point and contacts the
        table exactly when it has fallen h, so the documented conservative
        bias (the integrator under-reports sqrt(2gh) by ~0.5% at 0.75 m,
        ~1.5% at 0.1 m; test_drop_sim.py:513-523) is < 1% at h = 0.5.
        """
        height = 0.5
        m = 0.1
        result = simulate(m, self.CUBE_INERTIA, self.CUBE_SUPPORT, height,
                          surface="concrete", drop_count=1, test="drop",
                          orientation="flat")
        self.assertGreaterEqual(len(result["impacts"]), 1)
        speed = result["impacts"][0]["impact_speed_m_s"]
        expected = math.sqrt(2.0 * G_DROP_M_S2 * height)
        self.assertLessEqual(abs(speed - expected) / expected, 0.01)


class ImpactModelTests(unittest.TestCase):
    """Item 6: contact impulse, peak force, and duration of impact.py.

    Contact model found in impact.py (estimate_impact, the
    ``contact_stiffness_n_per_m`` branch, impact.py:583-592,
    CONTACT_MODEL_LINEAR):

        J      = m_eff*(1 + e)*v_n                     (impulse-momentum)
        F_peak = v_n*sqrt(m_eff*k)                     (linear spring)
        t      = (pi/2)*sqrt(m_eff/k)                  (compression phase)
        delta  = v_n*sqrt(m_eff/k)                     (spring amplitude)

    The branch matches the harmonic-oscillator contact closed forms exactly:
    the peak force is the half-cycle peak F = sqrt(k*m)*v, and the reported
    contact duration is the COMPRESSION quarter-period (pi/2)*sqrt(m/k) —
    half of the full half-cycle contact time pi*sqrt(m/k) (the documented
    convention: "t = pi*sqrt(m_eff/k)/2; contact duration covers the
    compression phase").  v comes from fall_height via v = sqrt(2*g*h) with
    g = 9.80665 (the impact module's documented default).
    """

    def test_impulse_matches_momentum_change(self):
        """J = m*v for a fully inelastic impact (e = 0), within 2%.

        v = sqrt(2*g*h) with g = 9.80665, h = 0.5, m = 0.1:
        J = 0.1*sqrt(2*9.80665*0.5).
        """
        m, h = 0.1, 0.5
        v = math.sqrt(2.0 * G_STANDARD_M_S2 * h)
        result = estimate_impact(mass_kg=m, fall_height_m=h)
        expected = m * v
        self.assertLessEqual(abs(result.impulse_n_s - expected) / expected, 0.02)

    def test_linear_spring_peak_force(self):
        """F_peak = v*sqrt(k*m) (half-cycle peak), within 2%.

        k = 50 000 N/m, m = 0.1, v = sqrt(2*g*h): F = 3.1316*sqrt(5000).
        """
        m, h, k = 0.1, 0.5, 50000.0
        v = math.sqrt(2.0 * G_STANDARD_M_S2 * h)
        result = estimate_impact(mass_kg=m, fall_height_m=h, contact_stiffness_n_per_m=k)
        self.assertEqual(result.contact_model, "linear")
        expected = v * math.sqrt(k * m)
        self.assertLessEqual(abs(result.peak_force_n - expected) / expected, 0.02)

    def test_linear_spring_contact_duration(self):
        """t = (pi/2)*sqrt(m/k) (compression phase), within 5%.

        The documented linear-spring duration covers the compression phase
        only: t = (pi/2)*sqrt(m/k) = 2.221e-3 s for k = 50 000, m = 0.1,
        exactly half of the full harmonic-oscillator half-cycle
        pi*sqrt(m/k) = 4.443e-3 s.
        """
        m, h, k = 0.1, 0.5, 50000.0
        v = math.sqrt(2.0 * G_STANDARD_M_S2 * h)
        result = estimate_impact(mass_kg=m, fall_height_m=h, contact_stiffness_n_per_m=k)
        expected = math.pi * math.sqrt(m / k) / 2.0
        self.assertLessEqual(abs(result.contact_duration_s - expected) / expected, 0.05)
        # Spring amplitude (max compression) also matches the closed form.
        expected_delta = v * math.sqrt(m / k)
        self.assertLessEqual(
            abs(result.contact_compression_m - expected_delta) / expected_delta, 0.05
        )


class StructuralDeformationTests(unittest.TestCase):
    """Item 7: beams and the plate against known closed forms."""

    def test_cantilever_point_deflection_formula(self):
        """w = F*L^3/(3*E*I), exact (closed-form beam solver)."""
        F, L, E, I = 10.0, 0.1, 200e9, 1e-8
        result = beam_response("cantilever_point", L_m=L, E_pa=E, I_m4=I,
                               A_m2=1e-4, nu=0.3, force_n=F)
        expected = F * L ** 3 / (3.0 * E * I)
        self.assertAlmostEqual(result.max_displacement_m, expected, places=10)

    def test_ss_uniform_beam_deflection_formula(self):
        """w = 5*q*L^4/(384*E*I), exact (closed-form beam solver)."""
        q, L, E, I = 100.0, 0.1, 200e9, 1e-8
        result = beam_response("simply_supported_uniform", L_m=L, E_pa=E, I_m4=I,
                               A_m2=1e-4, nu=0.3, q_n_per_m=q)
        expected = 5.0 * q * L ** 4 / (384.0 * E * I)
        self.assertAlmostEqual(result.max_displacement_m, expected, places=10)

    def test_plate_single_term_navier_deflection(self):
        """w_1 = 16*p/(D*pi^6*(1/a^2 + 1/b^2)^2), places=8.

        The order-1 (single-term) Navier response IS the closed-form
        single-term deflection of a simply supported square plate
        (w_1 = 16*p*a^4/(D*pi^6*4) for a = b), which is an upper bound on
        the full series.  D = E*t^3/(12*(1 - nu^2)).
        """
        a = b = 0.1
        t = 0.001
        E = 2.3e9
        nu = 0.35
        p = 5000.0
        D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
        result = shell_panel_response(a, b, t, E, nu, p, series_order=1)
        expected = 16.0 * p / (D * math.pi ** 6 * (1.0 / (a * a) + 1.0 / (b * b)) ** 2)
        self.assertAlmostEqual(result.max_displacement_m, expected, places=8)

    def test_ss_point_load_at_offset_matches_roark(self):
        """Simply supported beam, point load P at x0 (Roark Table 8.1 case 4).

        Reactions R1 = P*(L-x0)/L, R2 = P*x0/L; deflection at the load
        point w(x0) = P*x0^2*(L-x0)^2/(3*E*I*L); max shear
        V = max(R1, R2).  Verified against Roark's formulas for a
        concentrated intermediate load.
        """
        P, L, E, I, A = 10.0, 0.1, 200e9, 1e-8, 1e-4
        x0 = 0.03
        b = L - x0
        result = beam_response("simply_supported_point", L_m=L, E_pa=E, I_m4=I,
                               A_m2=A, nu=0.3, force_n=P, location_m=x0,
                               section_modulus_m3=1e-6)
        expected_w = P * x0 * x0 * b * b / (3.0 * E * I * L)
        self.assertAlmostEqual(result.max_displacement_m, expected_w, places=12)
        self.assertAlmostEqual(result.reactions["R1"], P * b / L, places=12)
        self.assertAlmostEqual(result.reactions["R2"], P * x0 / L, places=12)
        self.assertAlmostEqual(result.max_shear_pa, 1.5 * max(P * b / L, P * x0 / L) / A, places=12)
        # sigma = M/Z with Mmax = P*x0*(L-x0)/L (shear term negligible).
        moment = P * x0 * b / L
        sigma = moment / 1e-6
        tau = 1.5 * P * max(x0, b) / L / A
        self.assertAlmostEqual(result.max_stress_pa,
                               math.sqrt(sigma ** 2 + 3.0 * tau ** 2), places=6)

    def test_cantilever_point_load_at_offset_matches_roark(self):
        """Cantilever, point load P at distance a from the fixed end.

        Roark (Tables 8.1.1-8.1.2): Mmax = P*a at the support, deflection at
        the load point w(a) = P*a^3/(3*E*I), reaction moment M1 = -P*a.
        """
        P, L, E, I, A = 10.0, 0.1, 200e9, 1e-8, 1e-4
        a = 0.04
        result = beam_response("cantilever_point", L_m=L, E_pa=E, I_m4=I,
                               A_m2=A, nu=0.3, force_n=P, location_m=a,
                               section_modulus_m3=1e-6)
        expected_w = P * a ** 3 / (3.0 * E * I)
        self.assertAlmostEqual(result.max_displacement_m, expected_w, places=12)
        self.assertAlmostEqual(result.reactions["R1"], P, places=12)
        self.assertAlmostEqual(result.reactions["M1"], -P * a, places=12)
        self.assertAlmostEqual(result.max_shear_pa, 1.5 * P / A, places=12)
        sigma = P * a / 1e-6
        tau = 1.5 * P / A
        self.assertAlmostEqual(result.max_stress_pa,
                               math.sqrt(sigma ** 2 + 3.0 * tau ** 2), places=6)

    def test_ss_uniform_beam_max_shear_matches_analytic(self):
        """tau_max = 1.5*V_max/A with V_max = q*L/2, exact.

        Simply supported beam under uniform q: the maximum shear force is
        q*L/2 at the supports and the documented shear proxy is
        tau = 1.5*V/A (parabolic distribution over a rectangular section).
        """
        q, L, A = 100.0, 0.1, 1e-4
        result = beam_response("simply_supported_uniform", L_m=L, E_pa=200e9,
                               I_m4=1e-8, A_m2=A, nu=0.3, q_n_per_m=q)
        expected = 1.5 * (q * L / 2.0) / A
        self.assertAlmostEqual(result.max_shear_pa, expected, places=12)


class StressTests(unittest.TestCase):
    """Item 8: bending and plate stress vs closed forms."""

    def test_beam_bending_stress_moment_over_section_modulus(self):
        """sigma = M*c/I with M = F*L, c = I/Z, within 1%.

        Cantilever tip load F = 10 N, L = 0.1, I = 1e-8, Z = 2e-6
        (c = I/Z = 5e-3): sigma = F*L*c/I = F*L/Z = 5e5 Pa.  The reported
        stress is the von Mises combination of bending and the documented
        shear proxy tau = 1.5*V/A; with A = 1e-2 the shear term contributes
        < 1% and the reported value reduces to sigma = M*c/I.
        """
        F, L, E, I, Z, A = 10.0, 0.1, 200e9, 1e-8, 2e-6, 1e-2
        result = beam_response("cantilever_point", L_m=L, E_pa=E, I_m4=I,
                               A_m2=A, nu=0.3, force_n=F,
                               section_modulus_m3=Z)
        c = I / Z
        sigma_bending = F * L * c / I
        self.assertLessEqual(abs(result.max_stress_pa - sigma_bending) / sigma_bending, 0.01)
        # Exact closed form including the documented shear proxy.
        tau = 1.5 * F / A
        expected_vm = math.sqrt(sigma_bending ** 2 + 3.0 * tau ** 2)
        self.assertAlmostEqual(result.max_stress_pa, expected_vm, places=6)

    def test_plate_center_stress_matches_classical_coefficient(self):
        """sigma_center = beta*p*a^2/t^2, beta = 0.2874, within 2%.

        Converged (100-term, m,n = 1..99 odd) Navier double sum at the plate
        center for a simply supported square plate with nu = 0.3: the
        classical coefficient beta = 0.2874 (Roark; the beta table value is
        for nu = 0.3).  Both sides of the comparison are computed here from
        the written series and the written coefficient — the implementation
        is not involved.
        """
        a = b = 0.1
        t = 0.001
        E = 2.3e9
        nu = 0.3
        p = 5000.0
        D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
        mxx = myy = 0.0
        for m in range(1, 100, 2):
            for n in range(1, 100, 2):
                den = D * ((m / a) ** 4 + 2.0 * (m / a) ** 2 * (n / b) ** 2 + (n / b) ** 4)
                coeff = 16.0 * p / (math.pi ** 6 * m * n * den)
                s = math.sin(m * math.pi / 2.0) * math.sin(n * math.pi / 2.0)
                mxx += coeff * (math.pi * m / a) ** 2 * s
                myy += coeff * (math.pi * n / b) ** 2 * s
        mx = -(D * mxx + nu * D * myy)
        my = -(nu * D * mxx + D * myy)
        sx, sy = mx * 6.0 / (t * t), my * 6.0 / (t * t)
        series_vm = math.sqrt(max(0.0, sx * sx + sy * sy - sx * sy))
        classical = 0.2874 * p * a ** 2 / t ** 2
        self.assertLessEqual(abs(series_vm - classical) / classical, 0.02)

    def test_plate_reported_stress_matches_100_term_series(self):
        """Reported plate stress vs the 100-term Navier double sum, 0.5%.

        physics.py reports max_stress_pa as the von Mises maximum over the
        INTERIOR 3x3 grid nodes (i,j = 1..3; the 16 boundary nodes carry an
        idealized-SSSS corner twisting-moment artifact that over-reports the
        uniform-load peak by ~17%).  The reference here is the same quantity
        computed independently: the test's own 100-term double sum
        (m,n = 1..99 odd) evaluated on the same interior grid, maximizing
        the von Mises stress.  At the pipeline cap order (series_order=49)
        the solver matches the independent series within 0.5%.
        """
        a = b = 0.1
        t = 0.001
        E = 2.3e9
        nu = 0.3
        p = 5000.0
        D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
        grid_x = [a * i / 4.0 for i in range(5)]
        grid_y = [b * j / 4.0 for j in range(5)]
        mxx = [[0.0] * 5 for _ in range(5)]
        myy = [[0.0] * 5 for _ in range(5)]
        mxy = [[0.0] * 5 for _ in range(5)]
        for m in range(1, 100, 2):
            for n in range(1, 100, 2):
                den = D * ((m / a) ** 4 + 2.0 * (m / a) ** 2 * (n / b) ** 2 + (n / b) ** 4)
                coeff = 16.0 * p / (math.pi ** 6 * m * n * den)
                alpha = math.pi * m / a
                beta = math.pi * n / b
                for j, y in enumerate(grid_y):
                    siny = math.sin(beta * y)
                    cosy = math.cos(beta * y)
                    for i, x in enumerate(grid_x):
                        s = math.sin(alpha * x) * siny
                        mxx[j][i] += coeff * alpha * alpha * s
                        myy[j][i] += coeff * beta * beta * s
                        mxy[j][i] += coeff * alpha * beta * math.cos(alpha * x) * cosy
        best = 0.0
        factor = 6.0 / (t * t)
        for j in (1, 2, 3):
            for i in (1, 2, 3):
                mx = -(D * mxx[j][i] + nu * D * myy[j][i])
                my = -(nu * D * mxx[j][i] + D * myy[j][i])
                txy = D * (1.0 - nu) * mxy[j][i]
                sx, sy, sxy = mx * factor, my * factor, txy * factor
                vm = math.sqrt(max(0.0, sx * sx + sy * sy - sx * sy + 3.0 * sxy * sxy))
                if vm > best:
                    best = vm
        converged = shell_panel_response(a, b, t, E, nu, p, series_order=49)
        self.assertLessEqual(abs(converged.max_stress_pa - best) / best, 0.005)
        default = shell_panel_response(a, b, t, E, nu, p)
        self.assertLessEqual(abs(default.max_stress_pa - best) / best, 0.01)

    def test_plate_reported_stress_matches_classical_center_coefficient(self):
        """Reported max_stress_pa matches sigma = 0.2874*p*a^2/t^2, 1%.

        The interior-grid maximum for a uniformly loaded simply supported
        square panel (nu = 0.3) IS the classical center value
        (Roark coefficient beta = 0.2874).  At the default order 9 the
        truncated series is 0.14% above the classical value; at order 49 it
        is 0.03% below.  The corner twisting-moment artifact is reported
        separately as corner_twisting_vm_pa and is strictly larger.
        """
        a = b = 0.1
        t = 0.001
        p = 5000.0
        classical = 0.2874 * p * a ** 2 / t ** 2
        for order in (9, 49):
            result = shell_panel_response(a, b, t, 2.3e9, 0.3, p, series_order=order)
            self.assertLessEqual(
                abs(result.max_stress_pa - classical) / classical, 0.01, order
            )
            self.assertGreater(result.corner_twisting_vm_pa, result.max_stress_pa)


class UnitConsistencyTests(unittest.TestCase):
    """Item 9: unit conversions and the gravity conventions."""

    def test_newton_equals_kg_m_per_s2(self):
        """1 N = 1 kg*m/s^2: force, mass, length, time factors are
        dimensionally consistent and SI-normalized to 1."""
        self.assertEqual(UNIT_SPECS["N"].factor_to_si, 1.0)
        combined = (
            UNIT_SPECS["kg"].factor_to_si
            * UNIT_SPECS["m"].factor_to_si
            / UNIT_SPECS["s"].factor_to_si ** 2
        )
        self.assertEqual(UNIT_SPECS["N"].factor_to_si, combined)
        self.assertAlmostEqual(to_si(1.0, "N"), 1.0, places=12)
        self.assertAlmostEqual(
            to_si(1.0, "kg") * to_si(1.0, "m/s^2"), to_si(1.0, "N"), places=12
        )
        # 1 lbf = 1 lbm * g0, the same consistency identity at non-SI scale.
        self.assertAlmostEqual(
            UNIT_SPECS["lbf"].factor_to_si,
            UNIT_SPECS["lb"].factor_to_si * UNIT_SPECS["g0"].factor_to_si,
            places=12,
        )

    def test_gravity_conventions_documented_difference(self):
        """g0 = 9.80665 (units) vs 9.81 (drop_sim integrator): the 0.03%
        difference is documented, not asserted equal.

        drop_sim.py:19-21 documents: "Integrator gravity: 9.81 m/s^2
        (documented convention; g-UNIT conversions in the pipeline use the
        standard 9.80665 — the 0.03% difference is inert and disclosed)."
        """
        unit_g = UNIT_SPECS["g0"].factor_to_si
        self.assertAlmostEqual(unit_g, 9.80665, places=6)
        self.assertAlmostEqual(G_DROP_M_S2, 9.81, places=6)
        self.assertNotEqual(unit_g, G_DROP_M_S2)
        relative = abs(unit_g - G_DROP_M_S2) / unit_g
        self.assertLess(relative, 0.0005)
        self.assertGreater(relative, 0.0001)
        # A 1 kg body's weight: 9.81 N (drop integrator) vs 9.80665 N (units).
        self.assertAlmostEqual(to_si(1.0, "kg") * G_DROP_M_S2, 9.81, places=6)
        self.assertAlmostEqual(to_si(1.0, "kg") * unit_g, 9.80665, places=6)


if __name__ == "__main__":
    unittest.main()
