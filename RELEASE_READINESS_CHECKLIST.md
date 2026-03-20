# Axion Release Readiness Checklist

## Release Target: First-Client Delivery (V1.0)

### Core Product Promise
- [ ] End-to-end pipeline works: ingest → classify → collect → analyze → alert → digest
- [ ] Dashboard is functional and professional
- [ ] System starts cleanly from documented instructions
- [ ] Default configuration is safe and sensible

### Configuration & Setup
- [ ] .env loading works correctly from documented location
- [ ] All YAML config keys are modeled in Pydantic (no silently ignored keys)
- [ ] Orphaned config files removed or integrated
- [ ] Default auth is secure (not disabled)
- [ ] Default network binding is safe (not 0.0.0.0)

### Data Sources
- [ ] All enabled sources in sources.yaml have working parsers
- [ ] Sources with missing parsers are disabled or removed
- [ ] Source fetch errors are visible and diagnosable

### Security
- [ ] API auth enabled by default with sensible key generation guidance
- [ ] Default host binding is localhost (127.0.0.1), not 0.0.0.0
- [ ] No secrets in committed files
- [ ] HTTPS guidance documented

### Reliability
- [ ] Scheduler jobs have error isolation (confirmed)
- [ ] LLM failures gracefully degrade to rule-based fallbacks
- [ ] HTTP fetch timeouts are configured
- [ ] Database WAL mode verified at startup

### Testing
- [ ] All existing tests pass
- [ ] API endpoint smoke tests exist
- [ ] Core pipeline has integration test coverage

### Documentation
- [ ] Single clear installation guide (no conflicting docs)
- [ ] Operations guide is accurate
- [ ] Known limitations documented
- [ ] Troubleshooting guide is accurate

### Dashboard / UX
- [ ] No debug artifacts
- [ ] No hardcoded URLs
- [ ] Professional appearance
- [ ] Error states are visible

### Deployment
- [ ] Clean install path documented and verified
- [ ] Windows launcher works
- [ ] Docker deployment works
- [ ] macOS deployment documented

### Commercial
- [ ] Proprietary license notice present
- [ ] Version number is set
- [ ] No placeholder/demo markers in client-facing surfaces
