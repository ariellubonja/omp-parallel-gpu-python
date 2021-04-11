import matplotlib.pyplot as plt
import numpy as np
from sklearn.linear_model import OrthogonalMatchingPursuit
from sklearn.linear_model import OrthogonalMatchingPursuitCV
from sklearn.datasets import make_sparse_coded_signal
from scipy.io import loadmat
import random
from multiprocessing.dummy import Pool as ThreadPool
import multiprocessing
from tqdm import tqdm
import os
import torch
import torch.utils
import torch.utils.data
from torch.utils.data import *
from torch.utils.data.sampler import *

from contextlib import contextmanager
from timeit import default_timer

@contextmanager
def elapsed_timer():
    # https://stackoverflow.com/questions/7370801/how-to-measure-elapsed-time-in-python
    start = default_timer()
    elapser = lambda: default_timer() - start
    yield lambda: elapser()
    end = default_timer()
    elapser = lambda: end-start

if False:
    mat = loadmat('ps1_2021')
    Af = mat['Af']
    Ar = mat['Ar']
    yf = mat['yf']
    yr = mat['yr']
    omp = OrthogonalMatchingPursuit(n_nonzero_coefs=3)
    # X = np.random.randn(Af.shape[0], Af.shape[1])
    # X /= np.sqrt(np.sum((X ** 2), axis=0))
    omp.fit(Af, yf)
    print(omp.coef_)
    omp.fit(Ar, yr)
    print(omp.coef_)

# We can take a single solver, and multiple problems, and spread it out on processing units.
# ^ This can be done simply with the multiprocessing Python module.

n_components, n_features = 512, 100
n_nonzero_coefs = 17
n_samples = 10000

def solveomp(y):
    solveomp.omp.fit(solveomp.X, y)
    return solveomp.omp.coef_

def init_threads(func, X, omp_args):
    func.omp = OrthogonalMatchingPursuit(**omp_args)
    func.X = X


def algorithmV0(y, X, n_nonzero_coefs=None):
    # Based on https://github.com/zhuhufei/OMP/blob/master/codeAug2020.m
    # From "Efficient Implementations for Orthogonal Matching Pursuit" (2020)
    if n_nonzero_coefs is None:
        n_nonzero_coefs = X.shape[1]
    dictsize = X.shape[0]
    D = X.dot(X.T)
    # Initialize
    r = y
    L = np.zeros_like(X, shape=(n_nonzero_coefs, n_nonzero_coefs))
    gamma = np.zeros_like(X, shape=(n_nonzero_coefs, 1))

    projections = X.T @ y
    F = np.identity(n_nonzero_coefs, dtype=X.dtype)
    a_F = np.zeros_like(gamma)
    temp_F_k_k = 0

    innerp = {0: projections}
    amplitudes = {}
    D_mybest = np.zeros_like(X, D.shape[0], n_nonzero_coefs)
    s = np.zeros_like(X, (dictsize, 1))
    for k in range(n_nonzero_coefs):
        projsq = projections * projections
        maxindex = np.argmax(projsq)
        alpha = projsq[maxindex]
        newgam = maxindex[0]
        gamma[k] = newgam
        if k == 0:
            D_mybest[:, 1] = D[:, newgam]
            a_F[1] = projections(newgam)
            projections = projections - D_mybest[:, 1]*a_F[1]
            normr2 = normr2 - a_F[1] ^ 2
        else:
            temp_F_k_k = np.sqrt(1 / (1 - np.sum(D_mybest[newgam, :] * D_mybest[newgam,:])))
            F[:, k] = -temp_F_k_k * (F * D_mybest[newgam, :].T)
            F[k, k] = temp_F_k_k
            D_mybest[:, k]=temp_F_k_k * (D[:, newgam] - D_mybest * D_mybest[newgam,:].T);
            a_F[k] = temp_F_k_k * projections(newgam)
            projections = projections - D_mybest[:, k]*a_F[k]
            normr2 = normr2 - a_F[k] * a_F[k]

        amplitudes[k] = np.zeros_like(s)
        amplitudes[k][gamma[:k]] = F[:k, :k]*a_F[:k]
        k += 1
    pass

import scipy.sparse
def omp_naive(X, y, n_nonzero_coefs):
    Xt = np.ascontiguousarray(X.T)
    y = np.ascontiguousarray(y.T)
    r = y.copy()  # Maybe no transpose?
    sets = np.zeros((n_nonzero_coefs, r.shape[0]), dtype=np.int32)
    problems = np.zeros((r.shape[0], X.shape[0], n_nonzero_coefs))
    solutions = np.zeros((r.shape[0], n_nonzero_coefs))
    for k in range(n_nonzero_coefs):
        best_idxs = np.abs(Xt @ r[:, :, None]).squeeze(-1).argmax(1)
        # Indices of best columns
        sets[k, :] = best_idxs
        # Actual columns at those indexes
        problems[:, :, k] = Xt[best_idxs, :]
        # Matrix of the columns used to represent y
        current_problems = problems[:, :, :k+1]
        if False:
            for idx in range(r.shape[0]):
                # Safest:  solution, *_ = np.linalg.lstsq(current_problems[idx], y[idx], rcond=None)
                # Less safe (and slower): solution = np.linalg.pinv(current_problems[idx]) @ y[idx]
                solution = np.linalg.solve(current_problems[idx].T @ current_problems[idx], current_problems[idx].T @ y[idx])
                # ^ Fastest. Also safe since an orthonormal matrix is never badly conditioned.
                solutions[idx, :k+1] = solution
        current_problemst = current_problems.transpose([0, 2, 1])
        solutions[:, :k+1] = np.linalg.solve(current_problemst @ current_problems, current_problemst @ y[:, :, None]).squeeze(-1)
        r = y - (current_problems @ solutions[:, :k+1, None]).squeeze(-1)
        # maybe memoize in case y is large, such that probability of repeats is significant.
    #     We could get 20 matches of the "cache" for 10000 different random ys.
    # else:
    xests = np.zeros((r.shape[0], X.shape[1]))
    np.put_along_axis(xests, sets.T, solutions, -1)
    return xests


if __name__ == "__main__":
    # TODO: https://roman-kh.github.io/numpy-multicore/
    y, X, w = make_sparse_coded_signal(
        n_samples=n_samples,
        n_components=n_components,
        n_features=n_features,
        n_nonzero_coefs=n_nonzero_coefs,
        random_state=0)

    print("Settings used for the test: ")
    print("Number of Samples: " + str(n_samples))
    print("Number of Components: " + str(n_components))
    print("Number of Features: " + str(n_features))
    print("Number of Nonzero Coefficients: " + str(n_nonzero_coefs))
    print("\n")

    print('Single core. Naive Implementation, based on our Homework.')
    with elapsed_timer() as elapsed:
        xests = omp_naive(X, y, n_nonzero_coefs)
    print('Samples per second:', n_samples/elapsed())
    print("\n")
    # exit()
    # precompute=True seems slower for single core. Dunno why.
    omp_args = dict(n_nonzero_coefs=n_nonzero_coefs, precompute=False, fit_intercept=False)

    # Single core
    print('Single core. Sklearn')
    omp = OrthogonalMatchingPursuit(**omp_args)
    with elapsed_timer() as elapsed:
        omp.fit(X, y)
    print('Samples per second:', n_samples/elapsed())
    print("\n")

    naive_err = np.linalg.norm(y.T - (X @ xests[:, :, None]).squeeze(-1), 2, 1)
    scipy_err = np.linalg.norm(y.T - (X @ omp.coef_[:, :, None]).squeeze(-1), 2, 1)
    avg_ylen = np.linalg.norm(y, 2, 0)
    # print(np.median(naive_err) / avg_ylen, np.median(scipy_err) / avg_ylen)
    plt.plot(np.sort(naive_err / avg_ylen))
    plt.plot(np.sort(scipy_err / avg_ylen), '--')
    plt.legend(["Naive", "Scipy"])
    plt.title("Distribution of relative errors.")
    plt.show()
    exit(0)
    # Multi core
    no_workers = 2 # os.cpu_count()
    # TODO: Gramian can be calculated once locally, and sent to each thread.
    print('Multi core. With', no_workers, "workers on", os.cpu_count(), "(logical) cores.")
    inputs = np.array_split(y, no_workers, axis=-1)
    with multiprocessing.Pool(no_workers, initializer=init_threads, initargs=(solveomp, X, omp_args)) as p:  # num_workers=0
        with elapsed_timer() as elapsed:
            result = p.map(solveomp, inputs)
    print('Samples per second:', n_samples / elapsed())

    # dataset = RandomSparseDataset(n_samples, n_components, n_features, n_nonzero_coefs)
    # sampler = torch.utils.data.sampler.BatchSampler(SequentialSampler(dataset), batch_size=2, drop_last=False)

    # y_est = (X @ omp.coef_[:, :, np.newaxis]).squeeze(-1)
    # residuals = np.linalg.norm(y.T - y_est, 2, 0)

    # plt.hist(residuals)
    # plt.show()

