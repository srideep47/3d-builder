"""Neural intake — images + prompt → constrained deliverable.

Phase 8.6 (GLM_PROMPT_NEURAL_INTAKE.md): the pieces that turn a
multi-view photo set and an owner prompt into a routed, generated,
analysed and conformed delivery. This package holds the intake-side
logic (view diversity, the build-route router, the machine-wide GPU
lock) that is shared between the webapp, the agent loop and the img3d
service boundary; the ComfyUI TRELLIS 2 backend itself lives behind the
service's NeuralBackend ABC (services/img3d_service/providers/).
"""
