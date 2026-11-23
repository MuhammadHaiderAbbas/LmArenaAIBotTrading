# ML workspace

Placeholder structure for Phases 6–7. Nothing in this directory is imported by the running
application; model code must never be able to place an order.

```
ml/
├── datasets/     Versioned, time-stamped feature/label datasets (leakage-controlled)
├── features/     Feature engineering, transforms, point-in-time joins
├── models/       Serialised artefacts (git-ignored) + registry metadata
├── training/     Training entry points with reproducibility metadata
├── evaluation/   Walk-forward, out-of-sample and calibration reports
└── drift/        Feature-distribution and performance drift detectors
```

## Rules for anything that lands here

1. **No look-ahead.** Every feature must be computable at decision time; point-in-time joins only.
2. **Chronological splits.** Never random shuffle for time-series; walk-forward validation is required.
3. **Baseline first.** Compare against trivial baselines before claiming value.
4. **Cost realism.** Evaluate after fees, spread, slippage and financing.
5. **Immutable artefacts.** A promoted model is versioned with its dataset hash, feature set and
   evaluation record; promotion happens through an approval workflow with rollback.
6. **Calibration is a claim.** Confidence values are not probabilities until calibration is measured.
7. **No silent rewrites.** A model may propose changes; it cannot modify production trading rules.
8. **Isolation for RL.** Reinforcement-learning research stays isolated from production until
   independently validated.
