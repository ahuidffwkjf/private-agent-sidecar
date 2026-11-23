# Long-Term Trust Candidates

`GET /v1/peers/candidates` and `GET /v1/projects/<project_id>/candidates` expose candidate Harness nodes with a source label:

- `project-member`
- `global-trusted`
- `pending`
- `revoked`

A globally trusted node is visible as a candidate in every project, but visibility does not itself create a project trust grant. Selecting it in a project creates an explicit project-scoped grant; the global grant remains independent when the project closes.
