from __future__ import division, absolute_import, print_function
from past.builtins import xrange

import numpy as np
import os
import sys
import esutil
import time
import scipy.optimize

import matplotlib.pyplot as plt

from .fgcmUtilities import gaussFunction
from .fgcmUtilities import histoGauss
from .fgcmUtilities import objFlagDict

from .sharedNumpyMemManager import SharedNumpyMemManager as snmm

class FgcmSigFgcm(object):
    """
    Class to compute repeatability statistics for stars.

    parameters
    ----------
    fgcmConfig: FgcmConfig
    fgcmPars: FgcmParameters
    fgcmStars: FgcmStars

    Config variables
    ----------------
    sigFgcmMaxEGray: float
       Maxmimum m_std - <m_std> to consider to compute sigFgcm
    sigFgcmMaxErr: float
       Maxmimum error on m_std - <m_std> to consider to compute sigFgcm
    """

    def __init__(self,fgcmConfig,fgcmPars,fgcmStars):

        self.fgcmLog = fgcmConfig.fgcmLog

        self.fgcmLog.info('Initializing FgcmSigFgcm')

        # need fgcmPars because it has the sigFgcm
        self.fgcmPars = fgcmPars

        # need fgcmStars because it has the stars (duh)
        self.fgcmStars = fgcmStars

        # and config numbers
        self.sigFgcmMaxEGray = fgcmConfig.sigFgcmMaxEGray
        self.sigFgcmMaxErr = fgcmConfig.sigFgcmMaxErr
        self.plotPath = fgcmConfig.plotPath
        self.outfileBaseWithCycle = fgcmConfig.outfileBaseWithCycle
        self.cycleNumber = fgcmConfig.cycleNumber
        self.colorSplitIndices = fgcmConfig.colorSplitIndices

    def computeSigFgcm(self,reserved=False,doPlots=True,save=True,crunch=False):
        """
        Compute sigFgcm for all bands

        parameters
        ----------
        reserved: bool, default=False
           Use reserved stars instead of fit stars?
        doPlots: bool, default=True
        save: bool, default=True
           Save computed values to fgcmPars?
        crunch: bool, default=False
           Compute based on ccd-crunched values?
        """

        if (not self.fgcmStars.magStdComputed):
            raise ValueError("Must run FgcmChisq to compute magStd before computeCCDAndExpGray")

        startTime = time.time()
        self.fgcmLog.info('Computing sigFgcm.')

        # input numbers
        objID = snmm.getArray(self.fgcmStars.objIDHandle)
        objMagStdMean = snmm.getArray(self.fgcmStars.objMagStdMeanHandle)
        objMagStdMeanErr = snmm.getArray(self.fgcmStars.objMagStdMeanErrHandle)
        objNGoodObs = snmm.getArray(self.fgcmStars.objNGoodObsHandle)
        objFlag = snmm.getArray(self.fgcmStars.objFlagHandle)

        obsMagStd = snmm.getArray(self.fgcmStars.obsMagStdHandle)
        # obsMagErr = snmm.getArray(self.fgcmStars.obsMagADUErrHandle)
        obsMagErr = snmm.getArray(self.fgcmStars.obsMagADUModelErrHandle)
        obsBandIndex = snmm.getArray(self.fgcmStars.obsBandIndexHandle)

        objObsIndex = snmm.getArray(self.fgcmStars.objObsIndexHandle)
        obsObjIDIndex = snmm.getArray(self.fgcmStars.obsObjIDIndexHandle)
        obsExpIndex = snmm.getArray(self.fgcmStars.obsExpIndexHandle)
        obsFlag = snmm.getArray(self.fgcmStars.obsFlagHandle)

        # make sure we have enough obervations per band
        minObs = objNGoodObs[:,self.fgcmStars.bandRequiredIndex].min(axis=1)

        # select good stars...
        if (reserved):
            # only reserved stars
            goodStars,=np.where((minObs >= self.fgcmStars.minObsPerBand) &
                                ((objFlag & objFlagDict['RESERVED']) > 0))
            # FIXME need to remove BAD STARS as well
        else:
            # all good stars
            goodStars,=np.where((minObs >= self.fgcmStars.minObsPerBand) &
                                (objFlag == 0))

        # match the good stars to the observations
        _,goodObs = esutil.numpy_util.match(goodStars,
                                            obsObjIDIndex,
                                            presorted=True)

        # and make sure that we only use good observations from good exposures
        gd,=np.where((self.fgcmPars.expFlag[obsExpIndex[goodObs]] == 0) &
                     (obsFlag[goodObs] == 0))

        goodObs = goodObs[gd]

        # we need to compute E_gray == <mstd> - mstd for each observation
        # compute EGray, GO for Good Obs
        EGrayGO = (objMagStdMean[obsObjIDIndex[goodObs],obsBandIndex[goodObs]] -
                   obsMagStd[goodObs])
        # and need the error for Egray: sum in quadrature of individual and avg errs
        EGrayErr2GO = (obsMagErr[goodObs]**2. -
                       objMagStdMeanErr[obsObjIDIndex[goodObs],obsBandIndex[goodObs]]**2.)

        # now we can compute sigFgcm

        sigFgcm = np.zeros(self.fgcmStars.nBands)

        # and we do 4 runs: full, blue 25%, middle 50%, red 25%
        # FIXME: use filterToBand or related for this...
        gmiGO = (objMagStdMean[obsObjIDIndex[goodObs], self.colorSplitIndices[0]] -
               objMagStdMean[obsObjIDIndex[goodObs], self.colorSplitIndices[1]])
        st = np.argsort(gmiGO)
        gmiCutLow = np.array([gmiGO[st[0]],
                              gmiGO[st[0]],
                              gmiGO[st[int(0.25*st.size)]],
                              gmiGO[st[int(0.75*st.size)]]])
        gmiCutHigh = np.array([gmiGO[st[-1]],
                               gmiGO[st[int(0.25*st.size)]],
                               gmiGO[st[int(0.75*st.size)]],
                               gmiGO[st[-1]]])
        gmiCutNames = ['All','Blue25','Middle50','Red25']


        for bandIndex in xrange(self.fgcmStars.nBands):
            # start the figure which will have 4 panels
            fig = plt.figure(figsize=(9,6))
            fig.clf()

            started=False
            for c in xrange(gmiCutLow.size):
                if (bandIndex in self.fgcmStars.bandRequiredIndex):
                    sigUse,=np.where((np.abs(EGrayGO) < self.sigFgcmMaxEGray) &
                                     (EGrayErr2GO > 0.0) &
                                     (EGrayErr2GO < self.sigFgcmMaxErr**2.) &
                                     (EGrayGO != 0.0) &
                                     (obsBandIndex[goodObs] == bandIndex) &
                                     (gmiGO > gmiCutLow[c]) &
                                     (gmiGO < gmiCutHigh[c]))
                else:
                    sigUse,=np.where((np.abs(EGrayGO) < self.sigFgcmMaxEGray) &
                                     (EGrayErr2GO > 0.0) &
                                     (EGrayErr2GO < self.sigFgcmMaxErr**2.) &
                                     (EGrayGO != 0.0) &
                                     (obsBandIndex[goodObs] == bandIndex) &
                                     (objNGoodObs[obsObjIDIndex[goodObs],bandIndex] >=
                                      self.fgcmStars.minObsPerBand) &
                                     (gmiGO > gmiCutLow[c]) &
                                     (gmiGO < gmiCutHigh[c]))

                if (sigUse.size == 0):
                    self.fgcmLog.info('sigFGCM: No good observations in %s band (color cut %d).' %
                                     (self.fgcmPars.bands[bandIndex],c))
                    continue

                #fig = plt.figure(1,figsize=(8,6))
                #fig.clf()

                #ax=fig.add_subplot(111)
                ax=fig.add_subplot(2,2,c+1)

                try:
                    coeff = histoGauss(ax, EGrayGO[sigUse])
                except:
                    coeff = np.array([np.inf, np.inf, np.inf])

                if not np.isfinite(coeff[2]):
                    self.fgcmLog.info("Failed to compute sigFgcm (%s) (%s).  Setting to 0.05" %
                                     (self.fgcmPars.bands[bandIndex],gmiCutNames[c]))
                    sigFgcm[bandIndex] = 0.05
                elif (np.median(EGrayErr2GO[sigUse]) > coeff[2]**2.):
                    self.fgcmLog.info("Typical error is larger than width (%s) (%s).  Setting to 0.001" %
                                      (self.fgcmPars.bands[bandIndex],gmiCutNames[c]))
                    sigFgcm[bandIndex] = 0.001
                else:
                    sigFgcm[bandIndex] = np.sqrt(coeff[2]**2. -
                                                 np.median(EGrayErr2GO[sigUse]))

                self.fgcmLog.info("sigFgcm (%s) (%s) = %.4f" % (
                        self.fgcmPars.bands[bandIndex],
                        gmiCutNames[c],
                        sigFgcm[bandIndex]))

                if (save and (c==0)):
                    # only save if we're doing the full color range
                    self.fgcmPars.compSigFgcm[bandIndex] = sigFgcm[bandIndex]

                if (not doPlots):
                    continue

                ax.tick_params(axis='both',which='major',labelsize=14)

                text=r'$(%s)$' % (self.fgcmPars.bands[bandIndex]) + '\n' + \
                    r'$\mathrm{Cycle\ %d}$' % (self.cycleNumber) + '\n' + \
                    r'$\mu = %.5f$' % (coeff[1]) + '\n' + \
                    r'$\sigma_\mathrm{tot} = %.4f$' % (coeff[2]) + '\n' + \
                    r'$\sigma_\mathrm{FGCM} = %.4f$' % (sigFgcm[bandIndex]) + '\n' + \
                    gmiCutNames[c]

                ax.annotate(text,(0.95,0.93),xycoords='axes fraction',ha='right',va='top',fontsize=14)
                ax.set_xlabel(r'$E^{\mathrm{gray}}$',fontsize=14)

                if (reserved):
                    extraName = 'reserved'
                else:
                    extraName = 'all'

                if crunch:
                    extraName += '_crunched'

                ax.set_title(extraName)

                if (not started):
                    started=True
                    plotXRange = ax.get_xlim()
                else:
                    ax.set_xlim(plotXRange)

            fig.tight_layout()
            fig.savefig('%s/%s_sigfgcm_%s_%s.png' % (self.plotPath,
                                                     self.outfileBaseWithCycle,
                                                     extraName,
                                                     self.fgcmPars.bands[bandIndex]))
            plt.close()

        self.fgcmLog.info('Done computing sigFgcm in %.2f sec.' %
                         (time.time() - startTime))



