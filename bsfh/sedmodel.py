import numpy as np
from sedpy.observate import vac2air

class ThetaParameters(object):
    """
    Object describing a model parameter set, and conversions between a
    parameter dictionary and a theta vector (for use in MCMC sampling).
    Also contains a method for computing the prior probability of a given
    theta vector.

    It must be intialized with a theta_desc, a description of the theta
    vector including the prior functions for each theta.
    """
    def __init__(self, theta_desc=None, theta_init=None, **kwargs):
        
        self.theta_desc = theta_desc
        self.params = {}
        if theta_init:
            self.set_parameters(theta_init)
        for k,v in kwargs.iteritems():
            self.params[k] = np.atleast_1d(v)

        # Caching. No. only works if theta_desc is not allowed to
        #  change after intialization so, might as well set it here.
        self.ndim = 0
        for p, v in self.theta_desc.iteritems():
                self.ndim += v['N']
        
    def set_parameters(self, theta):
        """
        Propagate theta into the model parameters.
        """
        assert len(theta) == self.ndim
        for p, v in self.theta_desc.iteritems():
            start, end = v['i0'], v['i0'] + v['N']
            self.params[p] = np.array(theta[start:end])

    def theta_from_params(self):
        """
        Generate a theta vector from the parameter list and the theta
        descriptor.
        """

        theta = np.zeros(self.ndim)
        for p, v in self.theta_desc.iteritems():
            start, end = v['i0'], v['i0'] + v['N']
            theta[start:end] = self.params[p]
        return theta

    def prior_product(self, theta):
        """
        Return a scalar which is the ln of the product of the prior
        probabilities for each element of theta.  Requires that the
        prior functions are defined in the theta descriptor.

        :param theta:
            Iterable containing the free model parameter values.

        :returns lnp_prior:
            The log of the product of the prior probabilities for
            these parameter values.
        """
        
        lnp_prior = 0
        for p, v in self.theta_desc.iteritems():
            start, stop = v['i0'], v['i0'] + v['N']
            lnp_prior += np.sum(v['prior_function'](theta[start:stop], **v['prior_args']))
        return lnp_prior

    def lnp_prior_grad(self, theta):
        """
        Return a vector of gradients in the prior probability.
        Requires  that functions giving the gradients are given in the
        theta descriptor.
        """
        lnp_prior_grad = np.zeros_like(theta)
        for p, v in self.theta_desc.iteritems():
            start, stop = v['i0'], v['i0'] + v['N']
            lnp_prior_grad[start:stop] = v['prior_gradient_function'](theta[start:stop], **v['prior_args'])
        return lnp_prior_grad

    def check_constrained(self, theta):
        """
        For HMC, check if the trajectory has hit a wall in any
        parameter.   If so, reflect the momentum and update the
        parameter position in the  opposite direction until the
        parameter is within the bounds. Bounds  are specified via the
        'upper' and 'lower' keys of the theta descriptor
        """
        oob = True
        sign = np.ones_like(theta)
        if self.verbose: print('theta in={0}'.format(theta))
        while oob:
            oob = False
            for p,v in self.theta_desc.iteritems():
                start, end = v['i0'], v['i0'] + v['N']
                if 'upper' in v.keys():
                    above = theta[start:end] > v['upper']
                    oob = oob or np.any(above)
                    theta[start:end][above] = 2 * v['upper'] - theta[start:end][above]
                    sign[start:end][above] *= -1
                if 'lower' in v.keys():
                    below = theta[start:end] < v['lower']
                    oob = oob or np.any(below)
                    theta[start:end][below] = 2 * v['lower'] - theta[start:end][below]
                    sign[start:end][below] *= -1
        if self.verbose: print('theta out={0}'.format(theta))            
        return theta, sign, oob


    def bounds(self):
        bounds = self.ndim * [(0.,0.)]
        for p, v in self.theta_desc.iteritems():
            sz = np.size(v['prior_args']['mini'])
            if sz == 1:
                bounds[v['i0']] = (v['prior_args']['mini'], v['prior_args']['maxi'])
            else:
                for k in range(sz):
                    bounds[v['i0']+k] = (v['prior_args']['mini'][k],
                                         v['prior_args']['maxi'][k])
        return bounds
                

class SedModel(ThetaParameters):

    def add_obs(self, obs, rescale = True):
        self.filters = obs['filters']
        self.obs = obs
        #rescale the spectrum to avoid floating point errors
        if rescale:
            sc = np.median(obs['spectrum'][obs['mask']])
            self.obs['scale'] = sc
            self.obs['spectrum'] /= sc
            self.obs['unc'] /= sc
        else:
            self.obs['scale'] = 1.0

    def mean_model(self, theta, sps = None, **kwargs):
        
        """
        Given a theta vector, generate a spectrum, photometry, and any
        extras (e.g. stellar mass).

        :param theta:
            ndarray of parameter values.
            
        :param sps:
            A StellarPopulation or StellarPopBasis object to be used
            in the model generation.

        :returns spec:
            The model spectrum for these parameters, at the wavelengths
            specified by obs['wavelength'].
            
        :returns phot:
            The model photometry for these parameters, for the filters
            specified in obs['filters'].
            
        :returns extras:
            Any extra aspects of the model that are returned.
        """
        
        if sps is None:
            sps = self.sps
        self.set_parameters(theta)
        spec, phot, extras = sps.get_spectrum(self.params.copy(),
                                              self.obs['wavelength'],
                                              self.obs['filters'])
        spec *= self.params.get('normalization_guess',1.0)
        #remove negative fluxes
        tiny = 1.0/len(spec) * spec[spec > 0].min()
        spec[ spec < tiny ] = tiny

        #spec = (spec + self.sky()) * self.calibration()
        return spec, phot, extras

    def sky(self):
        """Model for the sky emission/absorption"""
        return 0.
        
    def calibration(self, theta=None):
        """
        Implements a polynomial calibration model.  This only happens
        if `pivot_wave` is a defined model parameter, since the
        polynomial is returned in terms of r'$x \equiv
        \lambda/\lambda_{{pivot}} - 1$'.

        :returns cal:
           a polynomial given by 'spec_norm' * (1 + \Sum_{m=1}^M
           'poly_coeffs'[m-1] x**m)
        """
        if theta is not None:
            self.set_parameters(theta)
        
        #should find a way to make this more generic
        if 'pivot_wave' in self.params:
            x = self.obs['wavelength']/self.params['pivot_wave'] - 1.0
            poly = np.zeros_like(x)
            powers = np.arange( len(self.params['poly_coeffs']) ) + 1
            poly = (x[None,:] ** powers[:,None] * self.params['poly_coeffs'][:,None]).sum(axis = 0)
        
            return (1.0 + poly) * self.params['spec_norm']
        else:
            return 1.0


class CSPModel(ThetaParameters):
    """
    For parameterized SFHs and magnitudes only.
    """
    
    def add_obs(self, obs, rescale = True):
        self.filters = obs['filters']
        self.obs = obs

    def mean_model(self, theta, sps = None, **kwargs):
        """
        :returns spec:
            A None type object, only included for consistency with the
            SedModel class.
            
        :returns phot:
            The apparent maggies per unit surviving stellar mass (not
            *formed* stellar mass) in each of the filters.

        :returns extras:
            A None type object, only included for consistency with the
            SedModel class.
        """
        self.set_parameters(theta)
        # Pass the model parameters through to the sps object
        for k,v in self.params.iteritems():
            if k in sps.params.all_params:
                sps.params[k] = v
        #now get the magnitudes and normalize by (current) stellar mass
        mags = sps.get_mags(redshift=sps.params['zred'],
                            bands=self.obs['filters'])
        mass_norm = self.params.get('mass',1.0)/sps.stellar_mass
        return None, mass * 10**(-0.4*mags), None

    def calibration(self):
        return 1.0
    
    def sky(self):
        return 0.
    
class Parameter(object):
    """
    For a possible switch from dictionaries to specialized objects for
    the parameters.  This would require a massive rewrite of
    ThetaParameters() and seems unnecessary, though a little cleaner
    and of course more OO.
    """
    def __init__(self, name, **kwargs):
        self.name = name
        self.prior_function_name = None
        self.prior_grad_function_name = None
        self.prior_args = {}
        self.isfree = False
        for k, v in kwargs.iteritems():
            setattr(self,k,v)

    #def _prior_function
            
    def lnp_prior(self, theta):
        try:
            return self.prior_function(theta, **self.prior_args)
        except(AttributeError):
            return 0
        
    @property
    def N(self):
        return np.size(self.value)

    @property
    def bounds(self):
        pass

    #@property
    #def isfree(self):
    #    return self.free
    
def gauss(x, mu, A, sigma):
    """
    Lay down mutiple gaussians on the x-axis.
    """ 
    mu, A, sigma = np.atleast_2d(mu), np.atleast_2d(A), np.atleast_2d(sigma)
    val = A/(sigma * np.sqrt(np.pi * 2)) * np.exp(-(x[:,None] - mu)**2/(2 * sigma**2))
    return val.sum(axis = -1)

