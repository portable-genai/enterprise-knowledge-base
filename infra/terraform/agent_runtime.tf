# agent_runtime.tf : Deliberately empty managed Agent Runtime authority boundary.
#
# The code retains a fail-closed port and adapter seam, but no reasoningEngine, service account,
# IAM grant, KMS grant or AlloyDB login is provisioned while immutable verified invocation
# context is unavailable. `managed_readiness.tf` refuses the future deployment toggle. When that
# bridge exists, add the workload identity and least privileges in the same reviewed change as
# the executable transport; do not reserve dormant production authority in advance.
