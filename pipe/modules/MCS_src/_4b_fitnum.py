#!/usr/bin/python
##############
import sys
sys.path.append("/home/epfl/tewes/localpymodules/lib64/python")

import os
import numpy as np
import pyfits
import pywt

def fromfits(filename):
	return pyfits.getdata(filename).transpose()

def extract(a, cx, cy, halfsize=32):
	return a[cx-halfsize:cx+halfsize, cy-halfsize:cy+halfsize]

def tofits(a, filename):
	#if os.path.exists(filename):
	#	os.remove(filename)
	pyfits.writeto(filename, a.transpose(), clobber=1)

class mywt():
	"""
	seen from the outside :
	level 1 = fine
	level n = coarse
	inside we use the pywt convention
	"""
	def __init__(self, inputarray, wavelet = "haar", mode="sym", levels = None):
		
		self.inputarray = inputarray
		self.wavelet = wavelet
		self.mode = mode
		
		self.levels = levels # Will be updated by decompose ...
		self.decompose()
		

	def __str__(self):
		
		infostrings = []
		infostrings.append("%i x %i pixels, %s, %i levels" % (self.inputarray.shape[0], self.inputarray.shape[1], self.wavelet, self.levels))
		infostrings.append("/".join(["%i" % (s) for s in self.levelsizes][::-1]))
		return "\n".join(infostrings)

	def decompose(self):
		self.coeffs = pywt.wavedec2(self.inputarray, wavelet = self.wavelet, level = self.levels)
		self.levels = len(self.coeffs)-1
		self.levelsizes = []
		for cg in self.coeffs[1:]: # the coeff groups, we skip the "last" averadge 
			self.levelsizes.append(cg[0].shape[0])
			
	def mutacoeffs(self):
		"""
		We transform the coeffs into lists, so that you can change them.
		"""
		tmp = self.coeffs
		self.coeffs = [tmp[0]]
		self.coeffs.extend([list(cg) for cg in tmp[1:]])

	def levelindex(self, level):
		return self.levels - level + 1
		
	def details(self, level):
		return self.coeffs[self.levelindex(level)]

	def reconstruct(self):	
		return pywt.waverec2(self.coeffs, self.wavelet, self.mode)
		
	def softshrink(self, level, t):
		
		for i in (0,1,2):
			coeffs = self.details(level)[i]
			zeroes = np.zeros(coeffs.shape)
			shrinks = np.abs(coeffs)-t
			self.details(level)[i] = np.sign(coeffs) * np.maximum(zeroes, shrinks)

	def hardshrink(self, level, t):
		
		for i in (0,1,2):
			#print "" % np.std(self.details(level)[i])
			self.details(level)[i][np.abs(self.details(level)[i]) <= t] = 0.0


	def mallat(self):
		"""
		only works for haar for now ...
		"""
		
		mallat = np.zeros(self.inputarray.shape)
		# The final avdge :
		centerpoint = self.levelsizes[0]
		xcp = centerpoint
		ycp = self.inputarray.shape[1]-centerpoint
		ls = self.levelsizes[0]
		mallat[xcp-ls:xcp, ycp:ycp+ls] = self.coeffs[0]
		
		# The details :
		for (i, ls) in enumerate(self.levelsizes):
			centerpoint = self.levelsizes[0] + int(np.sum(self.levelsizes[:i]))
			xcp = centerpoint
			ycp = self.inputarray.shape[1]-centerpoint
			#print i, ls, xcp, ycp
			
			mallat[xcp:xcp+ls, ycp-ls:ycp] = self.coeffs[i+1][2] # D
			mallat[xcp:xcp+ls, ycp:ycp+ls] = self.coeffs[i+1][0] # H
			mallat[xcp-ls:xcp, ycp-ls:ycp] = self.coeffs[i+1][1] # V
			
		return mallat



#############
from lib.AImage import *
from lib.Star import *
from lib.Param import *
from lib.Algorithms import *
import lib.utils as fn
import lib.wsutils as ws
from numpy import *
import numpy as np
import scipy.ndimage.interpolation, copy

out = fn.Verbose()

PAR = Param(1, 0)
STAR_COL = []
TRACE = []

def fitnum(fit_id, data, params, savedir = 'results/'):
    global PAR
    global STAR_COL
    global TRACE
    
    starpos, npix, sfactor, mpar = params['STARS'], params['NPIX'], params['S_FACT'], copy.deepcopy(params['MOF_PARAMS'])
    gres, fitrad, psf_size, itnb =  params['G_RES'], params['FIT_RAD'], params['PSF_SIZE'], params['MAX_IT_N']
    nbruns, show, lamb, stepfact = params['NB_RUNS'], params['SHOW'], params['LAMBDA_NUM'], params['BKG_STEP_RATIO_NUM']
    cuda, radius, stddev, objsize = params['CUDA'], params['PSF_RAD'], params['SIGMA_SKY'], params['OBJ_SIZE']
    center = params['CENTER']

    PAR = Param(1, 0)
    STAR_COL = []
    TRACE = []
    mofpar = Param(1, 0)
    bshape = data['stars'][fit_id][0].shape
    sshape = (int(bshape[0]*sfactor), int(bshape[1]*sfactor))
#    nbruns=1
    radius = (radius is None or radius==0.) and psf_size*1.42 or radius

    r_len = sshape[0]
#    c1, c2 =  r_len/2., r_len/2.
    c1, c2 =  r_len/2.-1., r_len/2.-1.
    center = 'SW'
    if cuda and fn.has_cuda():
        out(2, 'CUDA initializations')
        center = 'SW'
        context, plan = fn.cuda_init(sshape)
        r = fn.gaussian((r_len, r_len), gres, c1, c2, 1.)
        r = fn.switch_psf_shape(r, center)
        def conv(a, b):
            return fn.cuda_conv(plan, a, b)
    else:
#        r_len = math.pow(2.0,math.ceil(math.log(gres*10)/math.log(2.0)))
        nx = ny = r_len
        if center == 'O':
            c1, c2 = nx/2.-0.5, ny/2.-0.5
        elif center == 'NE':
            c1, c2 = nx/2., ny/2.
        elif center == 'SW':
            c1, c2 = nx/2.-1., ny/2.-1.
        conv = fn.conv
        r = fn.gaussian((r_len, r_len), gres, c1, c2, 1.)
        cuda = False
    r /= r.sum()
    
    for i, pos in enumerate(starpos):           
            #populates the stars array:
#            im = Image(copy.deepcopy(data['stars'][fit_id][i]), noisemap = copy.deepcopy(data['sigs'][fit_id][i])) 
            im = Image(data['stars'][fit_id][i].copy(), noisemap = data['sigs'][fit_id][i].copy()) 
            im.shiftToCenter(mpar[fit_id][4+3*i]/sfactor,mpar[fit_id][4+3*i+1]/sfactor, center_mode='O')
            mpar[fit_id][4+3*i:4+3*i+2] = sshape[0]/2., sshape[1]/2. 
            STAR_COL += [Star(i+1, im, mofpar, sfactor, gres, False)]
    mofpar.fromArray(array(mpar[fit_id]), i = -1)
    mof_err = 0.
    
    rc1, rc2 = sshape[0]/2., sshape[1]/2.
    #c = lambda x,y: (x-rc1)**2. + (y-rc2)**2. > radius**2.
    #mask = fromfunction(c, sshape)
    #lamb = mask*lamb/500. + (np.invert(mask))*lamb
    lamb = 0.000001
    lamb = np.ones(sshape) * lamb	# plain flat lambda
    
    #fn.array2ds9(lamb)
    ini = array([])
    for i, s in enumerate(STAR_COL):
#        c1, c2 = bshape[0]/2., bshape[1]/2.
#        c = lambda x,y: (x-c1)**2. + (y-c2)**2. <= radius**2.
#        mask = fromfunction(c, bshape)
#        s.image.noiseMap += s.image.noiseMap*(np.invert(mask))*stddev[i]*1000000.
#        s.image.noiseMap += s.image.noiseMap*(-mask)#*stddev[i]*1000000.
#        s.image.array *= mask
        
        s.moff_eval()
        s.build_diffm()
        
        s.image.noiseMap /= mpar[fit_id][6+3*(s.id-1)]
        s.diffm.array /= mpar[fit_id][6+3*(s.id-1)]
        ini = append(ini, fn.rebin(s.diffm.array, sshape)/sfactor**2.)
#        ini += fn.rebin(s.diffm.array/s.image.noiseMap, sshape)
        mof_err += abs(s.diffm.array).sum()
    
    ini = median(ini.reshape((len(STAR_COL),sshape[0]*sshape[1])), 0)
    
    
    
    def _errfun(bkg, null):
        global TRACE
        param = bkg.reshape(sshape) # le modele, petits pixels (sshape = small pixel shape)
        err = zeros(sshape, dtype=float64) # erreur sur le modele, petit pixels
        convo = conv(r, param) # param = le modele, convo = on convolue le modele avec une gaussienne, en petits pixels
	#convosmooth = conv(r, convo) # we conv again, to make it smoother
        convo_m = fn.mean(convo, bshape[0], bshape[1]) # we rebin this to the big pixels
	
	#khi_smooth = np.abs(param - convosmooth)			# in small pixels
	#print np.mean(khi_smooth)
	
	"""
	w = mywt(param, wavelet = "coif1", levels=None)
	w.mutacoeffs()
	w.hardshrink(1, 0.0003)
	w.hardshrink(2, 0.0003)
	w.hardshrink(3, 0.0003)
	w.hardshrink(3, 0.0003)
	highpass = (w.reconstruct() - param)**2.0			# in small pixels
	#highpasspix = len(highpass > 0.0)
	meanerrsmooth = np.mean(highpass)
	#err += highpass
	"""
        for s in STAR_COL:
	
	    resi = np.abs((s.diffm.array - convo_m)/s.image.noiseMap)	# the residues, in large pixels
	    khi_fit = lamb*fn.rebin(resi, sshape)			# the residues, rebinned to small pixels, scaled with lambda
	    err += khi_fit
	    
	    #print np.mean(khi_fit)
	meanerrtot = np.mean(err)
	print "     Smoothing ratio : %.3f" % (meanerrsmooth/meanerrtot)

        TRACE += [err.sum()]
        return err.ravel()
    
    out(2, 'Begin minimization procedure')
    t = time.time()
    
    
    npar = ini.copy()
    for i in xrange(nbruns):
        npar = minimi(_errfun, npar.ravel(),[], itnb=itnb//nbruns, stepfact=stepfact, )[0][0]
#                      minstep_px=0.1, maxstep_px=10000000.9)[0][0]
#        npar = minimi_num(_errfun, npar, itnb=itnb//nbruns, stepfact=stepfact)[0]
        out(2)
    bak = npar.reshape(sshape)
    out(2, 'Done in', time.time()-t,'[s]')
    
#    bak, lbak = minimi_brute(_errfun, ini, itnb=itnb)#, stddev=sigma[0]/100000.)
#    out(2)
#    out(2, 'Done in', time.time()-t,'[s]')
    
    convo = conv(r, bak)
    gaus_err = convo*0.
    for s in STAR_COL:
        gaus_err += fn.rebin(abs(s.diffm.array - fn.mean(convo, bshape[0], bshape[1])), sshape)/sfactor**2.
    gaus_err = gaus_err.sum()
    out(2, "indicative error gain:", 100*(1. - TRACE[-1]/TRACE[0]), "%")#gaus_err/mof_err), "%")  

    out(2, 'Building the final PSF...')
    #TODO: use the center option!!
    
    if psf_size is None:
        psf_size = objsize
#    psf = PSF((psf_size*sfactor, psf_size*sfactor), 
#              (c1+(psf_size*sfactor-sshape[0])/2., c2+(psf_size*sfactor-sshape[0])/2.))
#    psf.addMof_fnorm(mpar[fit_id][0:4]+[psf.c1, psf.c2, 1.])
    size = psf_size*sfactor, psf_size*sfactor
    psf, s = fn.psf_gen(size, bak, mpar[fit_id][:4], [[]], [[]], 'mixed', center)
    
#    nx = ny = psf_size*sfactor
#    psf = PSF((nx,ny), (c1,c2))
#    mof = mpar[fit_id][:4]
#    psf.set_finalPSF(mof, [[]], [[]], 'mixed', (c1, c2, 1.), center=center)
#    if psf_size*sfactor != bak.shape[0]:
#        out(2, 'Expanding the PSF...')
#        psf.array[psf.c1-bak.shape[0]//2 : psf.c1+bak.shape[0]//2,
#                  psf.c2-bak.shape[1]//2 : psf.c2+bak.shape[1]//2] += bak
#    else:
#        psf.array += bak
##    else:
##        psf = PSF((psf_size*sfactor, psf_size*sfactor))
##        psf.addMof_fnorm(mpar[fit_id][0:4]+[psf.c1, psf.c2, 1.])
##    #    psf.set_finalPSF(mpar[fit_id][0:4], [], [], 'mixed')
##        psf.array += bak
#    psf.normalize()
#    fn.switch_psf_shape(psf.array), center
    
    if savedir is not None:
        out(2, 'Writing PSF to disk...')
        Image(s).writetofits(savedir+"s_"+str(fit_id+1)+".fits")
        Image(psf.array).writetofits(savedir+"psf_"+str(fit_id+1)+".fits")
        fn.array2fits(bak, savedir+'psfnum.fits')
        for s in STAR_COL:
            resi = (s.diffm.array-fn.mean(conv(r,bak), bshape[0], bshape[1]))/s.image.noiseMap
            fn.array2fits(resi, savedir+"difnum%(n)02d.fits" % {'n': s.id})
    if show == True:
        out(2, 'Displaying results...')
        fn.array2ds9(psf.array, name='psf')
        fn.array2ds9(bak, name='psfnum', frame=2)
        i = 3
        for s in STAR_COL:
            s.diffm.showds9(ds9frame=i,imgname="difm_"+str(s.id))
            resi = (s.diffm.array-fn.mean(conv(r,bak), bshape[0], bshape[1]))/s.image.noiseMap
            fn.array2ds9(resi, frame=i+1, name="resi_"+str(s.id))
            i += 2
        import pylab as p
        p.figure(1)
        trace = array(TRACE)
        X = arange(trace.shape[0])
        p.title('Error evolution')
        p.plot(X, trace)
        p.show()
        if savedir is not None:
            p.savefig(savedir+'trace%(fnb)02d.png'% {'fnb':fit_id+1})

    if cuda:
        out(2, 'Freeing CUDA context...')
        context.pop()
    return 0


    
def main(argv=None):
    cfg = 'config.py'
    if argv is not None:
        sys.argv = argv
    opt, args = fn.get_args(sys.argv)
    if args is not None: cfg = args[0]
    if 's' in opt: 
        out.level = 0
    if 'v' in opt: 
        out.level = 2
    if 'd' in opt: 
        DEBUG = True
        out.level = 3
        out(1, '~~~ DEBUG MODE ~~~')
    if 'e' in opt: 
        import prepare
        prepare.main(['_3_fitmof.py', '-ce', cfg])
    if 'h' in opt:
        out(1, 'No help page yet!')
        return 0
    out(1, 'Begin background fit')
    PSF_SIZE = None
    SHOW = False
    f = open(cfg, 'r')
    exec f.read()
    f.close()
    vars = ['FILENAME', 'SHOW', 'STARS','NPIX', 'MOF_PARAMS', 
            'G_PARAMS', 'G_POS', 'G_STRAT', 'S_FACT', 'NOWRITE', 
            'G_RES', 'CENTER', 'IMG_GAIN', 'SIGMA_SKY',
            'SKY_BACKGROUND', 'G_SETTINGS', 'NB_RUNS', 'FIT_RAD']
    err = fn.check_namespace(vars, locals())
    if err > 0:
        return 1
    out(2, 'Restore data from extracted files')
    files, cat = ws.get_multiplefiles(FILENAME, 'img')
    fnb = len(cat)
    data = ws.restore(*ws.getfilenames(fnb))
    data['filenb'] = fnb
    data['starnb'] = len(STARS)
    gpar = []
    gpos = []
    for i in xrange(fnb):
        out(1, '===============', i+1, '/', fnb,'===============')
        out(1, 'Working on', files[i])
        fitnum( i, data, locals())
    out(1, '------------------------------------------')
    out(1, 'Numerical fit done')
#    if NOWRITE is False:
#        fn.write_cfg(cfg, {'G_PARAMS':gpar, 'G_POS':gpos})
    return 0
    
    
def profile():
    # This is the main function for profiling 
    import cProfile, pstats
    prof = cProfile.Profile()
    prof = prof.runctx("main()", globals(), locals())
    stats = pstats.Stats(prof)
    stats.sort_stats("time")  # Or cumulative
    stats.print_stats(15)  # how many to print
    # The rest is optional.
    #stats.print_callees()
    #stats.print_callers()
    
if __name__ == "__main__":
    #sys.exit(profile())
    sys.exit(main())

    
