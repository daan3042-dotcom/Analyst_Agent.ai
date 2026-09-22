"""
simple_hmm.py
Een compacte, zelfstandige Gaussian Hidden Markov Model-implementatie in
pure NumPy -- gebouwd als vervanging voor de hmmlearn-package. Reden:
hmmlearn heeft op een recente Python-versie (3.14) nog geen kant-en-klare,
voorgecompileerde versie, en probeert dan zelf te bouwen vanaf de
broncode -- wat een C++-compiler vereist (Microsoft Visual C++ Build
Tools) die de meeste mensen niet standaard hebben. NumPy zelf heeft die
eis niet (heeft al een voorgecompileerde versie voor Python 3.14), dus
dit voorkomt het probleem volledig in plaats van er omheen te werken.

Dekt precies wat compute_regime_detection() (in data_fetch.py) nodig
heeft: fit een N-staats Gaussian HMM op een 1D-reeks (dagelijkse
rendementen), en geef de geschatte parameters + de meest waarschijnlijke
toestand-per-dag terug (Viterbi-decodering, net als hmmlearn's eigen
.predict()) -- geen bredere, algemene HMM-bibliotheek, bewust beperkt tot
dit ene gebruiksdoel.
"""

import numpy as np


def _gaussian_pdf(x: np.ndarray, mean: float, var: float) -> np.ndarray:
    """Kansdichtheid van een 1D-normale verdeling -- met een ondergrens op
    de variantie om een deling door (bijna) nul te voorkomen als een
    toestand toevallig heel weinig spreiding toegewezen krijgt."""
    var = max(var, 1e-10)
    return np.exp(-0.5 * (x - mean) ** 2 / var) / np.sqrt(2 * np.pi * var)


def fit_gaussian_hmm(observations: np.ndarray, n_states: int, n_iter: int = 200,
                      random_state: int = 42) -> dict:
    """Schat een Gaussian HMM via Baum-Welch (EM), met schaling voor
    numerieke stabiliteit over lange reeksen (~750 dagen is heel normaal),
    en geeft de meest waarschijnlijke toestand-per-tijdstip terug via
    Viterbi-decodering -- dezelfde aanpak als hmmlearn's GaussianHMM.

    observations: 1D-array (of iets dat daartoe herleid kan worden) van
    floats, bijv. dagelijkse rendementen.

    Geeft terug: {"means": [...], "variances": [...], "hidden_states": [...]}
    -- een lijst van 0..n_states-1 per waarneming, dezelfde vorm als
    hmmlearn's model.predict()."""
    x = np.asarray(observations, dtype=float).flatten()
    T = len(x)
    if T < n_states * 2:
        raise ValueError(f"te weinig waarnemingen ({T}) om {n_states} toestanden te schatten")

    # Initialisatie: verdeel de GESORTEERDE waarnemingen in n_states even
    # grote groepen, en gebruik het gemiddelde/de variantie van elke groep
    # als startpunt -- veel stabieler dan puur willekeurige initialisatie
    # (voorkomt dat EM in een slecht lokaal optimum vastloopt), en dicht bij
    # wat hmmlearn's eigen k-means-achtige initialisatie ook doet.
    np.random.default_rng(random_state)  # vastgelegd voor reproduceerbaarheid, ook al is de init zelf deterministisch
    sorted_idx = np.argsort(x)
    groups = np.array_split(sorted_idx, n_states)
    means = np.array([x[g].mean() for g in groups])
    variances = np.array([max(x[g].var(), 1e-6) for g in groups])
    transmat = np.full((n_states, n_states), 1.0 / n_states)
    startprob = np.full(n_states, 1.0 / n_states)

    prev_log_likelihood = None
    for _ in range(n_iter):
        # --- E-stap: forward-backward, met schaling per tijdstip ---
        emission = np.column_stack([_gaussian_pdf(x, means[i], variances[i]) for i in range(n_states)])
        emission = np.clip(emission, 1e-300, None)  # nooit precies 0 -- dat zou de hele reeks vernietigen

        alpha = np.zeros((T, n_states))
        c = np.zeros(T)  # schaalfactoren, voorkomen underflow over lange reeksen
        alpha[0] = startprob * emission[0]
        c[0] = alpha[0].sum() or 1e-300
        alpha[0] /= c[0]
        for t in range(1, T):
            alpha[t] = (alpha[t - 1] @ transmat) * emission[t]
            c[t] = alpha[t].sum() or 1e-300
            alpha[t] /= c[t]

        beta = np.zeros((T, n_states))
        beta[-1] = 1.0
        for t in range(T - 2, -1, -1):
            beta[t] = (transmat @ (emission[t + 1] * beta[t + 1])) / c[t + 1]

        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)

        xi_sum = np.zeros((n_states, n_states))
        for t in range(T - 1):
            xi_sum += (alpha[t][:, None] * transmat * emission[t + 1][None, :] * beta[t + 1][None, :]) / c[t + 1]

        # --- M-stap ---
        startprob = gamma[0]
        denom = gamma[:-1].sum(axis=0)[:, None]
        transmat = xi_sum / np.clip(denom, 1e-300, None)
        transmat /= transmat.sum(axis=1, keepdims=True)  # hernormaliseren tegen afrondingsdrift
        for i in range(n_states):
            weight = gamma[:, i]
            weight_sum = weight.sum() or 1e-300
            means[i] = (weight * x).sum() / weight_sum
            variances[i] = max((weight * (x - means[i]) ** 2).sum() / weight_sum, 1e-6)

        log_likelihood = np.log(c).sum()
        if prev_log_likelihood is not None and abs(log_likelihood - prev_log_likelihood) < 1e-6:
            break
        prev_log_likelihood = log_likelihood

    # --- Viterbi-decodering: de meest waarschijnlijke ENKELE toestandsreeks
    # (in tegenstelling tot gamma hierboven, dat per tijdstip los de meest
    # waarschijnlijke toestand geeft zonder rekening te houden met een
    # consistent pad) -- dit is ook wat hmmlearn's .predict() teruggeeft. ---
    emission = np.column_stack([_gaussian_pdf(x, means[i], variances[i]) for i in range(n_states)])
    emission = np.clip(emission, 1e-300, None)
    log_startprob = np.log(np.clip(startprob, 1e-300, None))
    log_transmat = np.log(np.clip(transmat, 1e-300, None))
    log_emission = np.log(emission)

    viterbi = np.zeros((T, n_states))
    backpointer = np.zeros((T, n_states), dtype=int)
    viterbi[0] = log_startprob + log_emission[0]
    for t in range(1, T):
        scores = viterbi[t - 1][:, None] + log_transmat  # (n_states_from, n_states_to)
        backpointer[t] = np.argmax(scores, axis=0)
        viterbi[t] = scores[backpointer[t], np.arange(n_states)] + log_emission[t]

    states = np.zeros(T, dtype=int)
    states[-1] = np.argmax(viterbi[-1])
    for t in range(T - 2, -1, -1):
        states[t] = backpointer[t + 1, states[t + 1]]

    return {
        "means": means.tolist(),
        "variances": variances.tolist(),
        "hidden_states": states.tolist(),
        "transmat": transmat.tolist(),
    }
