import numpy as np
from numpy.polynomial.chebyshev import chebval, chebvander
from .parameters import ProspectorParams

__all__ = ["SedModel", "PolySedModel"]


class SedModel(ProspectorParams):
    """For models composed of SSPs and sums of SSPs which use the
    sps_basis.StellarPopBasis as the sps object.
    """

    def mean_model(self, theta, obs, sps=None, **extras):
        """Given a theta vector, generate a spectrum, photometry, and any
        extras (e.g. stellar mass), including any calibration effects.

        :param theta:
            ndarray of parameter values.

        :param obs:
            An observation dictionary, containing the output
            wavelength array, the photometric filter lists, and the
            key 'logify_spectrum' which is True if the comparison to
            the model is to be made in the log.

        :param sps:
            A StellarPopBasis object to be used
            in the model generation.

        :returns spec:
            The model spectrum for these parameters, at the wavelengths
            specified by obs['wavelength'], and optionally in the log.

        :returns phot:
            The model photometry for these parameters, for the filters
            specified in obs['filters'].

        :returns extras:
            Any extra aspects of the model that are returned.
        """
        # print('HJD: mean_model: theta => {}'.format(theta))
        s, p, x = self.sed(theta, obs, sps=sps, **extras)
        self._speccal = self.spec_calibration(obs=obs, **extras)
        if obs.get('logify_spectrum', False):
            s = np.log(s) + np.log(self._speccal)
        else:
            s *= self._speccal
        return s, p, x

    def sed(self, theta, obs, sps=None, **kwargs):
        """Given a theta vector, generate a spectrum, photometry, and any
        extras (e.g. stellar mass), ***not** including any instrument
        calibration effects.

        :param theta:
            ndarray of parameter values.

        :param sps:
            A StellarPopBasis object to be used
            in the model generation.

        :returns spec:
            The model spectrum for these parameters, at the wavelengths
            specified by obs['wavelength'], in linear units.

        :returns phot:
            The model photometry for these parameters, for the filters
            specified in obs['filters'].

        :returns extras:
            Any extra aspects of the model that are returned.
        """
        # print('HJD: sed: theta => {}'.format(theta))
        self.set_parameters(theta)
        spec, phot, extras = sps.get_spectrum(outwave=obs['wavelength'],
                                              filters=obs['filters'],
                                              component=obs.get('component', -1),
                                              lnwavegrid=obs.get('lnwavegrid', None),
                                              **self.params)

        spec *= obs.get('normalization_guess', 1.0)
        # Remove negative fluxes.
        try:
            tiny = 1.0/len(spec) * spec[spec > 0].min()
            spec[spec < tiny] = tiny
        except:
            pass
        spec = (spec + self.sky())
        self._spec = spec.copy()
        return spec, phot, extras

    def sky(self):
        """Model for the *additive* sky emission/absorption"""
        return 0.

    def spec_calibration(self, theta=None, obs=None, **kwargs):
        """Implements a Chebyshev polynomial calibration model. If
        ``"pivot_wave"`` is not present in ``obs`` then 1.0 is returned.

        :returns cal:
           If ``params["cal_type"]`` is ``"poly"``, a polynomial given by
           'spec_norm' * (1 + \Sum_{m=1}^M 'poly_coeffs'[m-1] T_n(x)).
           Otherwise, the exponential of a Chebyshev polynomial.
        """
        if theta is not None:
            self.set_parameters(theta)

        if ('poly_coeffs' in self.params):
            mask = obs.get('mask', slice(None))
            # map unmasked wavelengths to the interval -1, 1
            # masked wavelengths may have x>1, x<-1
            x = obs['wavelength'] - (obs['wavelength'][mask]).min()
            x = 2.0 * (x / (x[mask]).max()) - 1.0
            # get coefficients.  Here we are setting the first term to 0 so we
            # can deal with it separately for the exponential and regular
            # multiplicative cases
            c = np.insert(self.params['poly_coeffs'], 0, 0)
            poly = chebval(x, c)
            # switch to have spec_norm be multiplicative or additive depending
            # on whether the calibration model is multiplicative in exp^poly or
            # just poly
            if self.params.get('cal_type', 'exp_poly') is 'poly':
                return (1.0 + poly) * self.params.get('spec_norm', 1.0)
            else:
                return np.exp(self.params.get('spec_norm', 0) + poly)
        else:
            return 1.0 * self.params.get('spec_norm', 1.0)

    def spec_gp_params(self, theta=None, **extras):
        if theta is not None:
            self.set_parameters(theta)
        pars = ['gp_jitter', 'gp_amplitude', 'gp_length']
        defaults = [[0.0], [0.0], [1.0]]
        vals = [self.params.get(p, d) for p, d in zip(pars, defaults)]
        return tuple(vals)

    def phot_gp_params(self, theta=None, **extras):
        if theta is not None:
            self.set_parameters(theta)
        s = self.params.get('phot_jitter', 0.0)
        return s, [0.0], [0]


# This is a subclass of SedModel that replaces the calibration vector with the
# maximum likelihood chebyshev polynomial describing the difference between the
# observed and the model spectrum
class PolySedModel(SedModel):

    def spec_calibration(self, theta=None, obs=None, **kwargs):
        """Implements a Chebyshev polynomial calibration model. This uses
        least-squres to find the *optimal* Chebyshev polynomial of a certain
        order describing the ratio of the observed spectrum to the model
        spectrum, conditional on all other parameters, using least squares.
        The first coefficient is always set to 1, as the overall normalization
        is controlled by ``spec_norm``.

        :returns cal:
           A polynomial given by 'spec_norm' * (1 + \Sum_{m=1}^M
           'poly_coeffs'[m-1] T_n(x)).  Otherwise, the exponential of a
           Chebyshev polynomial.
        """
        if theta is not None:
            self.set_parameters(theta)

        polyopt = ((self.params.get('polyorder', 0) > 0) &
                   (obs.get('spectrum', None) is not None))
        if polyopt:
            order = self.params['polyorder']
            mask = obs.get('mask', slice(None))
            # map unmasked wavelengths to the interval -1, 1
            # masked wavelengths may have x>1, x<-1
            x = obs['wavelength'] - (obs['wavelength'][mask]).min()
            x = 2.0 * (x / (x[mask]).max()) - 1.0
            y = (obs['spectrum'] / self._spec)[mask] - 1.0
            yerr = (obs['unc'] / self._spec)[mask]
            yvar = yerr**2
            A = chebvander(x[mask], order)[:, 1:]
            ATA = np.dot(A.T, A / yvar[:, None])
            reg = self.params.get('poly_regularization', 0.)
            if np.any(reg > 0):
                ATA += reg**2 * np.eye(order)
            ATAinv = np.linalg.inv(ATA)
            c = np.dot(ATAinv, np.dot(A.T, y / yvar))
            Afull = chebvander(x, order)[:, 1:]
            poly = np.dot(Afull, c)

            return (1.0 + poly) * self.params.get('spec_norm', 1.0)
        else:
            return 1.0


def gauss(x, mu, A, sigma):
    """
    Sample multiple gaussians at positions x.

    :param x:
        locations where samples are desired.

    :param mu:
        Center(s) of the gaussians.

    :param A:
        Amplitude(s) of the gaussians, defined in terms of total area.

    :param sigma:
        Dispersion(s) of the gaussians, un units of x.

    :returns val:
        The values of the sum of gaussians at x.
    """
    mu, A, sigma = np.atleast_2d(mu), np.atleast_2d(A), np.atleast_2d(sigma)
    val = A / (sigma * np.sqrt(np.pi * 2)) * np.exp(-(x[:, None] - mu)**2 / (2 * sigma**2))
    return val.sum(axis=-1)
