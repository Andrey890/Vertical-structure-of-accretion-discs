#!/usr/bin/env python3
"""
Module contains several classes that represent vertical structure of accretion disc in case
of analytical opacity and ideal gas EOS.

Class IdealKramersVerticalStructure --  for Kramers opacity law and ideal gas EOS.
Class IdealBellLin1994VerticalStructure -- for opacity laws from (Bell & Lin, 1994) and ideal gas EOS.

"""
from collections import namedtuple
from enum import IntEnum

import numpy as np
from astropy import constants as const
from scipy.integrate import solve_ivp, simps
from scipy.optimize import brentq

sigmaSB = const.sigma_sb.cgs.value
R_gas = const.R.cgs.value
G = const.G.cgs.value
M_sun = const.M_sun.cgs.value
c = const.c.cgs.value


class Vars(IntEnum):
    """
    Enumerate that contains names of unknown functions.
    All functions are dimensionless.

    Attributes
    ----------
    S
        Mass coordinate.
    P
        Pressure.
    Q
        Flux of energy.
    T
        Temperature.
    """

    S = 0
    P = 1
    Q = 2
    T = 3


class BaseVerticalStructure:
    """
    Base class for Vertical structure, solver of the system of dimensionless vertical structure ODEs.
    The system contains four linear differential equations for pressure P, mass coordinate S, energy flux Q
    and temperature T as functions of vertical coordinate z. The only unknown free parameter
    is semi-thickness of accretion disc z_0. System is supplemented by four first-type
    boundary conditions (one for each variable). Method `fit` serve to find the free
    parameter z_0 and get solve the system. Integration of system is carried out by `integrate` method.

    Attributes
    ----------
    Mx : double
        Mass of central star in grams.
    alpha : double
        Alpha parameter for alpha-prescription of viscosity.
    r : double
        Distance from central star (radius in cylindrical coordinate system) in cm.
    F : double
        Moment of viscosity forces in g*cm^2/s^2.
    eps : double, optional
        Accuracy of vertical structure calculation.
    mu : double, optional
        Molecular weight for ideal gas equation of state.

    Methods
    -------
    fit()
        Solve optimization problem and calculate the vertical structure.
    integrate()
        Integrate the system and return values of four dimensionless functions.
    Pi_finder()
        Return the Pi values (see Ketsaris & Shakura, 1998).
    parameters_C()
        Calculates parameters of disc in the symmetry plane.
    tau()
        Calculates optical thickness of the disc.

    """

    def __init__(self, Mx, alpha, r, F, eps=1e-5, mu=0.6):
        self.mu = mu
        self.Mx = Mx
        self.GM = G * Mx
        self.alpha = alpha
        self.r = r
        self.F = F
        self.omegaK = np.sqrt(self.GM / self.r ** 3)
        self.eps = eps

        self.Q_norm = self.Q0 = (3 / (8 * np.pi)) * F * self.omegaK / self.r ** 2

        self.z0 = self.z0_init()
        self.Teff = (self.Q0 / sigmaSB) ** (1 / 4)

    @property
    def z0(self):
        return self.__z0

    @z0.setter
    def z0(self, z0):
        self.__z0 = z0
        self.P_norm = (4 / 3) * self.Q_norm / (self.alpha * z0 * self.omegaK)
        self.T_norm = self.omegaK ** 2 * self.mu * z0 ** 2 / R_gas
        self.sigma_norm = 28 * self.Q_norm / (3 * self.alpha * z0 ** 2 * self.omegaK ** 3)

    def law_of_viscosity(self, P):
        return self.alpha * P

    def law_of_rho(self, P, T, full_output):
        raise NotImplementedError

    def law_of_opacity(self, rho, T, lnfree_e):
        raise NotImplementedError

    def viscosity(self, y):
        return self.law_of_viscosity(y[Vars.P] * self.P_norm)

    def rho(self, y, full_output):
        return self.law_of_rho(y[Vars.P] * self.P_norm, y[Vars.T] * self.T_norm, full_output=full_output)

    def opacity(self, y, lnfree_e):
        rho = self.rho(y, full_output=False)
        return self.law_of_opacity(rho, y[Vars.T] * self.T_norm, lnfree_e=lnfree_e)

    def photospheric_pressure_equation(self, tau, P):
        T = self.Teff * (1 / 2 + 3 * tau / 4) ** (1 / 4)
        rho, eos = self.law_of_rho(P, T, True)
        varkappa = self.law_of_opacity(rho, T, lnfree_e=eos.lnfree_e)
        return self.z0 * self.omegaK ** 2 / varkappa

    def P_ph(self):
        solution = solve_ivp(
            self.photospheric_pressure_equation,
            [0, 2 / 3],
            [1e-7 * self.P_norm], rtol=self.eps
        )
        return solution.y[0][-1]

    def Q_initial(self):
        return 1

    def initial(self):
        """
        Initial conditions.

        Returns
        -------
        array

        """

        Q_initial = self.Q_initial()
        y = np.empty(4, dtype=np.float64)
        y[Vars.S] = 0
        y[Vars.P] = self.P_ph() / self.P_norm
        y[Vars.Q] = Q_initial
        y[Vars.T] = (Q_initial * self.Q_norm / sigmaSB) ** (1 / 4) / self.T_norm
        return y

    def dlnTdlnP(self, y, t):
        raise NotImplementedError

    def dQdz(self, y, t):
        w_r_phi = self.viscosity(y)
        return -(3 / 2) * self.z0 * self.omegaK * w_r_phi / self.Q_norm

    def dydt(self, t, y):
        """
        The right side of ODEs system.

        Parameters
        ----------
        t : array-like
            Modified vertical coordinate (t = 1 - z).
        y :
            Current values of (dimensionless) unknown functions.

        Returns
        -------
        array

        """
        dy = np.empty(4)
        if y[Vars.T] < 0 or y[Vars.S] < 0:
            print('S or T < 0')
            breakpoint()
        rho, eos = self.rho(y, full_output=True)
        dy[Vars.S] = 2 * rho * self.z0 / self.sigma_norm
        dy[Vars.P] = rho * (1 - t) * self.omegaK ** 2 * self.z0 ** 2 / self.P_norm
        dy[Vars.Q] = self.dQdz(y, t)
        grad = self.dlnTdlnP(y, t)
        dy[Vars.T] = grad * dy[Vars.P] * y[Vars.T] / y[Vars.P]
        return dy

    def integrate(self, t):
        """
        Integrates ODEs and return list that contains array with values of
        four dimentsionless functions and a message from the solver.

        Parameters
        ----------
        t : array-like
            Interval of integration and evaluation.

        Returns
        -------
        list
            List containing the array with values of dimentionless functions
            calculating at points of `t` array. Also list contains the
            message from the integrator.

        """
        assert t[0] == 0
        solution = solve_ivp(self.dydt, (t[0], t[-1]), self.initial(), t_eval=t, rtol=self.eps, method='RK23')
        return [solution.y, solution.message]

    def tau(self):
        """
        Calculates optical thickness of the disc.

        Returns
        -------
        double
            Optical thickness.

        """
        t = np.linspace(0, 1, 100)
        y = self.integrate(t)[0]
        rho, eos = self.rho(y, full_output=True)
        varkappa = self.opacity(y, lnfree_e=eos.lnfree_e)
        tau_norm = simps(varkappa * rho, t)
        return self.z0 * tau_norm

    def y_c(self):
        y = self.integrate([0, 1])
        return y[0][:, -1]

    def parameters_C(self):
        """
        Calculates parameters of disc in the symmetry plane.

        Returns
        -------
        array
            Opacity, bulk density, temperature, gas pressure and surface density of disc.

        """
        y_c = self.y_c()
        Sigma0 = y_c[Vars.S] * self.sigma_norm
        T_C = y_c[Vars.T] * self.T_norm
        P_C = y_c[Vars.P] * self.P_norm
        rho_C, eos = self.rho(y_c, full_output=True)
        varkappa_C = self.opacity(y_c, lnfree_e=eos.lnfree_e)
        return np.array([varkappa_C, rho_C, T_C, P_C, Sigma0])

    def tau0(self):
        y = self.parameters_C()
        Sigma0 = y[4]
        varkappa_C = y[0]
        return Sigma0 * varkappa_C / 2

    def Pi_finder(self):
        """
        Calculates the so-called Pi parameters (see Ketsaris & Shakura, 1998).

        Returns
        -------
        array
            Contains the values of Pi.

        """
        varkappa_C, rho_C, T_C, P_C, Sigma0 = self.parameters_C()

        Pi_1 = (self.omegaK ** 2 * self.z0 ** 2 * rho_C) / P_C
        Pi_2 = Sigma0 / (2 * self.z0 * rho_C)
        Pi_3 = (3 / 4) * (self.alpha * self.omegaK * P_C * Sigma0) / (self.Q0 * rho_C)
        Pi_4 = (3 / 32) * (self.Teff / T_C) ** 4 * (Sigma0 * varkappa_C)

        Pi_real = np.array([Pi_1, Pi_2, Pi_3, Pi_4])

        return Pi_real

    def z0_init(self):
        return (self.r * 2.86e-7 * self.F ** (3 / 20) * (self.Mx / M_sun) ** (-9 / 20)
                * self.alpha ** (-1 / 10) * (self.r / 1e10) ** (1 / 20))

    def fit(self):
        """
        Solve optimization problem and calculate the vertical structure.

        Returns
        -------
        double and result
            The value of normalized unknown free parameter z_0 / r and result of optimization.

        """

        def dq(z0r):
            self.z0 = z0r * self.r
            q_c = self.y_c()[Vars.Q]
            return q_c

        z0r = self.z0 / self.r
        sign_dq = dq(z0r)
        if sign_dq > 0:
            factor = 2.0
        else:
            factor = 0.5

        while True:
            z0r *= factor
            if sign_dq * dq(z0r) < 0:
                break

        z0r, result = brentq(dq, z0r, z0r / factor, full_output=True)
        return z0r, result


class RadiativeTempGradient:
    """
    Temperature gradient class. Calculate radiative d(lnT)/d(lnP) in the Eddington approximation.

    """
    def dlnTdlnP(self, y, t):
        rho, eos = self.rho(y, full_output=True)
        varkappa = self.opacity(y, lnfree_e=eos.lnfree_e)

        if t == 1:
            dlnTdlnP_rad = - self.dQdz(y, t) * (y[Vars.P] / y[Vars.T] ** 4) * 3 * varkappa * (
                    self.Q_norm * self.P_norm / self.T_norm ** 4) / (16 * sigmaSB * self.z0 * self.omegaK ** 2)
        else:
            dTdz = (abs(y[Vars.Q]) / y[Vars.T] ** 3) * 3 * varkappa * rho * self.z0 * self.Q_norm / (
                    16 * sigmaSB * self.T_norm ** 4)
            dPdz = rho * (1 - t) * self.omegaK ** 2 * self.z0 ** 2 / self.P_norm
            dlnTdlnP_rad = (y[Vars.P] / y[Vars.T]) * (dTdz / dPdz)
        return dlnTdlnP_rad


class IdealGasMixin:
    def law_of_rho(self, P, T, full_output):
        if not full_output:
            return P * self.mu / (R_gas * T)
        else:
            eos_temp = namedtuple(
                'eos_temp',
                ('lnfree_e',), )
            eos = eos_temp(0.0)
            return P * self.mu / (R_gas * T), eos


class KramersOpacityMixin:
    varkappa0 = 5e24
    zeta = 1
    gamma = -7 / 2

    def law_of_opacity(self, rho, T, lnfree_e):
        return self.varkappa0 * (rho ** self.zeta) * (T ** self.gamma)


class BellLin1994TwoComponentOpacityMixin:
    varkappa0_ff = 1.5e20  # BB AND FF, OPAL
    zeta_ff = 1
    gamma_ff = - 5 / 2
    varkappa0_h = 1.0e-36  # H-scattering
    zeta_h = 1 / 3
    gamma_h = 10

    def opacity_h(self, rho, T):
        return self.varkappa0_h * (rho ** self.zeta_h) * (T ** self.gamma_h)

    def opacity_ff(self, rho, T):
        return self.varkappa0_ff * (rho ** self.zeta_ff) * (T ** self.gamma_ff)

    def law_of_opacity(self, rho, T, lnfree_e):
        return np.minimum(self.opacity_h(rho, T), self.opacity_ff(rho, T))


class IdealKramersVerticalStructure(IdealGasMixin, KramersOpacityMixin, RadiativeTempGradient, BaseVerticalStructure):
    """
    Vertical structure class for Kramers opacity law and ideal gas EOS.

    """
    pass


class IdealBellLin1994VerticalStructure(IdealGasMixin, BellLin1994TwoComponentOpacityMixin, RadiativeTempGradient,
                                        BaseVerticalStructure):
    """
    Vertical structure class for opacity laws from (Bell & Lin, 1994) and ideal gas EOS.

    """
    pass


def main():
    M = 10 * M_sun
    alpha = 0.01
    Mdot = 1e17
    rg = 2 * G * M / c ** 2
    r = 400 * rg
    print('Finding Pi parameters of structure and making a structure plot. '
          '\nStructure with Kramers opacity and ideal gas EOS.')
    print('M = {:g} grams \nr = {:g} cm = {:g} rg \nalpha = {:g} \nMdot = {:g} g/s'.format(M, r, r / rg, alpha, Mdot))
    h = np.sqrt(G * M * r)
    r_in = 3 * rg
    F = Mdot * h * (1 - np.sqrt(r_in / r))
    vs = IdealKramersVerticalStructure(M, alpha, r, F)
    z0r, result = vs.fit()
    if result.converged:
        print('The vertical structure has been calculated successfully.')
    Pi = vs.Pi_finder()
    print('Pi parameters =', Pi)
    print('z0/r = ', z0r)
    t = np.linspace(0, 1, 100)
    S, P, Q, T = vs.integrate(t)[0]
    import matplotlib.pyplot as plt
    plt.plot(1 - t, S, label='S/S0')
    plt.plot(1 - t, P, label='P/P0')
    plt.plot(1 - t, Q, label='Q/Q0')
    plt.plot(1 - t, T, label='T/T0')
    plt.xlabel('z/z0')
    plt.grid()
    plt.legend()
    plt.show()
    return


if __name__ == '__main__':
    main()
