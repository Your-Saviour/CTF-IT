# AI-Generated CTF Modules - Research & Implementation Plan

**Created**: 2026-08-10
**Status**: Planned, awaiting implementation

## Research Summary

### Current System Architecture

**Module System**: YAML-based with optional shell scripts, supporting 6 types (vulnerability, hardening, payload, application_external, application_internal, goal)

**AI Agent**: EGATS planner with UCB node selection, TDI difficulty scoring, and Caldera integration for red team operations

**Module Structure**: Rich metadata including CALDERA tactics, verification specs, hints, conflicts, requires, and goal-specific scoring fields

**Module Organization**: Nested directory structure by type (modules/vulns/, modules/goals/, modules/application_external/, etc.)

### Key Research Findings

**AI-Resistant CTF Design** (2026 best practices):
- Custom application logic beats recycled patterns
- Stateful, multi-stage challenges resist automation
- Novel scenarios with no public writeups require genuine reasoning
- Dynamic environments and real-time interaction raise the bar

**AI-Generated Code Security Framework**:
- Author-time controls: steering documents, reviewed specifications, scoped tools
- Build-time controls: SAST, SCA, secrets detection, quality gates
- Human review for security-sensitive changes
- Audit trails and provenance tracking

**Module Quality Requirements**:
- Must pass Caldera generation requirements
- Verification specs must be testable
- Script execution must be isolated and safe
- Dependencies must be from trusted sources

## Implementation Plan

### Phase 1: AI Module Generation Core (Weeks 1-2)

**1.1 Module Generation Service**
- Create `ai_agent/services/module_generator.py` with:
  - Structured output schema for module YAML
  - Template-based YAML generation with LLM
  - Script generation with security best practices
  - Caldera metadata generation (tactic, technique, command structure)

**1.2 API Integration**
- Add `POST /admin/ai-agent/generate-module` endpoint
- Accept parameters: module_type, category, difficulty, points, description, constraints
- Return: generated YAML, script, Caldera metadata, validation warnings

**1.3 Module Storage**
- Add `ModuleDraft` model to agent database for review workflow
- Store generated modules with version history
- Enable admin approval before deployment

### Phase 2: Module Review & Validation (Weeks 2-3)

**2.1 Review Workflow**
- Admin approval page for generated modules
- Side-by-side comparison with original requirements
- Security review checklist integration

**2.2 Validation Suite**
- Create `builder/module_validator.py` with:
  - YAML schema validation (reuse existing module_loader logic)
  - Caldera compatibility checks (tactic/technique validity)
  - Verification spec validation
  - Script safety checks (no dangerous commands, proper error handling)
  - Dependency validation (no hallucinated packages)

**2.3 Automated Testing**
- Integration test that deploys generated modules to test VM
- Verification spec test execution
- Caldera ability generation test

### Phase 3: Attack Tree Integration (Week 3-4)

**3.1 Auto-Module Assignment**
- Extend EGATS planner to generate modules during planning
- When node is selected, propose new module if gaps exist
- Module generation integrated into action planning

**3.2 Attack Tree Updates**
- Auto-add generated modules to attack tree
- Update phase assignments based on kill chain
- Recompute attack paths with new nodes

**3.3 Dynamic Module Discovery**
- Scan for newly generated modules
- Auto-add to available pool
- Update quota selectors

### Phase 4: Advanced Features (Week 4-5)

**4.1 Module Templates**
- Create library of module templates by category
- Preset constraints for common vulnerability types
- Customizable parameters for variety

**4.2 Module Quality Scoring**
- Track module playability metrics (solve rate, time to solve)
- Score based on AI-resistance principles
- Recommend improvements for low-scoring modules

**4.3 A/B Testing Framework**
- Deploy generated modules as A/B tests
- Compare against human-written modules
- Track engagement and difficulty metrics

### Phase 5: Security & Operations (Week 5-6)

**5.1 Security Controls**
- LLM guardrails for module generation
- Input sanitization for all LLM outputs
- Audit logging for all generated modules
- Rate limiting on generation endpoints

**5.2 CI/CD Integration**
- Automated scanning of generated modules
- Dependency checks (SAST/SCA)
- Integration with existing build pipeline

**5.3 Monitoring & Observability**
- Track module generation volume
- Monitor approval/rejection rates
- Alert on suspicious generation patterns

## Key Design Decisions

### Module Quality Gates

1. YAML schema validation
2. Caldera compatibility check
3. Verification spec testability
4. Script execution safety
5. Dependency validation
6. Human approval required

### AI Agent Integration Points

- New endpoint for module generation
- Extended session manager with module context
- Enhanced EGATS planner with auto-generation
- State store updates for new modules

### Security Approach

- Author-time steering for module generation constraints
- Build-time validation before deployment
- Human review for security-sensitive modules
- Audit trails for all changes

### Module Types to Generate

- **Vulnerabilities**: SSH credentials, SUID binaries, writable files, weak configs
- **Goals**: C2 installation, defacement, credential exfiltration
- **Payloads**: Shellcode, backdoors, persistence mechanisms
- **Applications**: Custom vulnerable services (if infrastructure allows)

### Generation Strategy

1. Start with goal modules (highest value, clear success criteria)
2. Add vulnerabilities that support goals
3. Expand to hardening modules for completeness
4. Add application modules as infrastructure allows

## Risk Mitigation

### High Risk Areas

- Script injection vulnerabilities: Use strict output sanitization
- Caldera command hallucinations: Validate against known patterns
- Module conflicts: Auto-resolve conflicts during generation
- Quality regression: Automated testing before deployment

### Mitigation Strategies

- LLM output validation with regex patterns
- Caldera ability schema validation
- Test VM deployment for all generated modules
- Version history for easy rollback

## Success Metrics

- Module generation throughput (modules per session)
- Approval rate (human vs auto-approval)
- Module quality scores (playability, AI-resistance)
- Caldera integration success rate
- Security scan pass rate

## References

### Research Articles
1. "How We Built a Reliable CTF Platform (and Designed Challenges to Resist LLMs)" - SecureCircuit (2026-03-20)
2. "Why frontier LLMs solve your CTF challenges in minutes (and how to fix it)" - Authon Blog (2026-05-17)
3. "Why Pure-LLM CTFs Don't Work: A Hybrid Architecture for AI Security Challenges" - Wraith (2026-04-23)
4. "CTFs in the AI Era" - Include Security Blog (2026-04-23)
5. "How to Design CTF Challenges That AI Can't Solve" - Simulations Labs (2026-06-21)

### Security Frameworks
1. OWASP LLM Security Checklist (2026)
2. OWASP LLMSVS v2.0
3. AWS Security Blog: "Balancing speed and safety: A control framework for AI coding agents"
4. OpenSSF Best Practices: "Security-Focused Guide for AI Code Assistant Instructions"

### Implementation Files (for reference)
- `modules/vulns/weak_ssh_credentials/weak_ssh_credentials.yaml` - Example vulnerability module
- `modules/goals/deface_website/deface_website.yaml` - Example goal module
- `modules/goals/install_c2/install_c2.yaml` - Example C2 goal module
- `ai_agent/llm/client.py` - LLM client for structured JSON responses
- `ai_agent/planner/egats.py` - EGATS planner with attack tree integration
- `builder/module_loader.py` - Module loading and validation
- `builder/caldera.py` - Caldera plugin generation

## Next Steps

1. Review this plan and prioritize phases based on current needs
2. Decide on LLM provider and API configuration for module generation
3. Create initial module templates for common vulnerability types
4. Set up validation infrastructure before generating any modules
5. Run pilot generation with human review before auto-approval

## Questions for Future Discussion

1. Should AI-generated modules be auto-deployed or require admin approval?
2. What's the target module quality threshold before auto-approval?
3. Should we prioritize specific module categories or let AI decide?
4. How do we handle module conflicts between AI-generated and human-written modules?
5. Should generated modules be open-source or kept internal?
