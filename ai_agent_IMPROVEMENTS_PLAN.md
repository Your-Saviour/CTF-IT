# AI Agent Improvements Plan

## Overview
This document outlines planned improvements to the AI pentesting agent system in the CTF platform. The goal is to enhance robustness, maintainability, security, and user experience.

## 1. Planner Algorithm Improvements

### 1.1 Multi-Target Support
**File**: `ai_agent/planner/egats.py`

**Description**: Add target prioritization based on attack surface analysis.

**Implementation**:
- Add `prioritize_targets()` method to EGATSPlanner
- Score targets by open ports, services, and historical success rate
- Integrate with Caldera to scan targets before selection
- Update `plan_next_action()` to select best target first

**Benefits**:
- More intelligent target selection
- Better attack surface coverage
- Prioritizes high-value targets

### 1.2 Better Failure Handling
**File**: `ai_agent/planner/egats.py`

**Description**: Add retry logic and failure recovery for operations.

**Implementation**:
- Add `max_retries` parameter to `execute_action()`
- Implement exponential backoff for retries
- Log failures and adjust strategy based on failure patterns
- Update `record_result()` to track failure counts

**Benefits**:
- More resilient operation execution
- Better handling of transient failures
- Improved debugging with detailed failure logs

### 1.3 Adaptive Budget Allocation
**File**: `ai_agent/planner/egats.py`

**Description**: Dynamically adjust step budget based on session progress.

**Implementation**:
- Add `_progress_ratio()` method to calculate completion percentage
- Modify `__init__()` to set `budget_remaining` based on progress
- Consider session goals and time remaining

**Benefits**:
- More efficient use of budget
- Better session completion rates
- Adaptive to different session types

## 2. Context Management Enhancements

### 2.1 Structured Context
**File**: `ai_agent/memory/context.py`

**Description**: Improve context assembly with better organization.

**Implementation**:
- Refactor `assemble()` to return structured dictionary
- Add `vm_info` section with VM-specific context
- Add `goals` section with goal status
- Update LLM prompt to use structured format

**Benefits**:
- Better LLM understanding of context
- More relevant information for decision-making
- Improved context compression

### 2.2 Context Compression
**File**: `ai_agent/memory/context.py`

**Description**: Implement context compression for long sessions.

**Implementation**:
- Add `compress()` method to summarize long evidence lists
- Implement evidence aggregation by type
- Add threshold-based compression triggers
- Preserve critical information while reducing token count

**Benefits**:
- Better token efficiency
- Reduced context window usage
- Maintains important information

### 2.3 VM-Specific Context
**File**: `ai_agent/memory/context.py`

**Description**: Add VM-specific context to LLM queries.

**Implementation**:
- Add `_build_vm_context()` method
- Fetch VM info from state store (OS, open ports, packages)
- Add current user information
- Include network context (interfaces, routes)

**Benefits**:
- Better understanding of target environment
- More informed attack decisions
- Improved exploitation planning

## 3. Caldera Integration Improvements

### 3.1 Better Operation Management
**File**: `ai_agent/tools/caldera.py`

**Description**: Add operation monitoring and status tracking.

**Implementation**:
- Add `_get_operation_status()` method
- Implement operation monitoring loop
- Add operation health checks
- Return operation status with results

**Benefits**:
- Better visibility into operation progress
- Improved error detection
- Better user feedback

### 3.2 Ability Execution with Retry
**File**: `ai_agent/tools/caldera.py`

**Description**: Add retry logic and timeout handling for ability execution.

**Implementation**:
- Add timeout parameter to `_execute_ability()`
- Implement retry logic with exponential backoff
- Add operation status polling
- Return detailed execution status

**Benefits**:
- More reliable ability execution
- Better handling of timeouts
- Improved debugging

### 3.3 Batch Operations
**File**: `ai_agent/tools/caldera.py`

**Description**: Support executing multiple abilities in a single operation.

**Implementation**:
- Add `batch_execute_abilities()` method
- Create operation with multiple abilities
- Return results for all abilities
- Add batch operation monitoring

**Benefits**:
- More efficient Caldera usage
- Faster execution of related abilities
- Better coordination of multi-step attacks

## 4. State Store Enhancements

### 4.1 State Aggregation
**File**: `ai_agent/memory/state_store.py`

**Description**: Improve state organization by aggregating by source node.

**Implementation**:
- Modify `get_all()` to aggregate by source_node_id
- Return nested structure with source as top-level key
- Maintain backward compatibility

**Benefits**:
- Better state organization
- Easier querying by source
- Improved context assembly

### 4.2 State Querying
**File**: `ai_agent/memory/state_store.py`

**Description**: Add filtering capabilities to state queries.

**Implementation**:
- Add `query()` method with filters
- Support filtering by entity type, status, and other fields
- Add JSON-based filtering
- Return filtered results

**Benefits**:
- More flexible state queries
- Better state filtering
- Improved state management

## 5. Session Management Improvements

### 5.1 Session Templates
**File**: `ai_agent/services/session_manager.py`

**Description**: Add session templates for common use cases.

**Implementation**:
- Add `_get_template()` method with predefined templates
- Add `create_session_from_template()` method
- Support template overrides
- Add template metadata (description, use case)

**Benefits**:
- Easier session creation
- Consistent session setup
- Better user experience

### 5.2 Session Monitoring
**File**: `ai_agent/services/session_manager.py`

**Description**: Add real-time session status monitoring.

**Implementation**:
- Add `get_session_status()` method
- Check for pending actions
- Get VM status
- Estimate completion time
- Add health checks

**Benefits**:
- Better session visibility
- Improved monitoring
- Better user experience

### 5.3 Auto-Resume
**File**: `ai_agent/services/session_manager.py`

**Description**: Add checkpoint and resume functionality.

**Implementation**:
- Add checkpoint saving in `stop_session()`
- Add `resume_session()` method
- Restore tree state, state store, and budget
- Update session status
- Add checkpoint metadata

**Benefits**:
- Session persistence
- Better error recovery
- Improved user experience

## 6. Security Enhancements

### 6.1 Input Validation
**File**: `ai_agent/tools/caldera.py`

**Description**: Add comprehensive input validation.

**Implementation**:
- Add `validate_target()` method
- Define VALID_TARGET_PATTERNS for IP, domain, UUID
- Add validation in all input methods
- Return validation errors with context

**Benefits**:
- Better input sanitization
- Reduced injection risks
- Improved error messages

### 6.2 Rate Limiting
**File**: `ai_agent/routes/sessions.py`

**Description**: Add rate limiting to prevent abuse.

**Implementation**:
- Add `RateLimiter` class
- Track requests per key
- Implement time-based limits
- Add rate limit headers
- Return 429 on rate limit exceeded

**Benefits**:
- Better API protection
- Reduced abuse
- Improved performance

### 6.3 Sanitization Improvements
**File**: `ai_agent/tools/caldera.py`

**Description**: Enhance existing sanitization with more patterns.

**Implementation**:
- Add more comprehensive SAFE_NAME_PATTERN
- Add SAFE_ID_PATTERN improvements
- Add SAFE_IP_PATTERN
- Update all sanitization methods
- Add validation helpers

**Benefits**:
- Better input sanitization
- Reduced injection risks
- Improved security

## 7. Observability & Monitoring

### 7.1 Structured Logging
**File**: `ai_agent/planner/egats.py`

**Description**: Add structured logging for better debugging.

**Implementation**:
- Add timestamp to all logs
- Add session_id to all logs
- Add structured JSON format
- Write to both file and database
- Add log rotation

**Benefits**:
- Better debugging
- Improved monitoring
- Better compliance

### 7.2 Metrics Collection
**File**: `ai_agent/services/session_manager.py`

**Description**: Add Prometheus metrics for monitoring.

**Implementation**:
- Add ACTION_COUNTER metric
- Add SESSION_DURATION histogram
- Add ACTION_LATENCY histogram
- Add METRIC labels for better filtering
- Export metrics endpoint

**Benefits**:
- Better monitoring
- Improved observability
- Better performance tracking

## 8. Testing Improvements

### 8.1 Mock Caldera Integration Tests
**File**: `tests/test_agent_integration.py`

**Description**: Add unit tests with mocked Caldera integration.

**Implementation**:
- Create mock Caldera fixture
- Test operation creation
- Test ability execution
- Test error handling
- Test retry logic

**Benefits**:
- Better test coverage
- Faster test execution
- Isolated testing

### 8.2 Unit Tests for New Features
**File**: `tests/test_agent_unit.py`

**Description**: Add unit tests for new planner and context features.

**Implementation**:
- Test target prioritization
- Test context assembly
- Test context compression
- Test state querying
- Test session templates

**Benefits**:
- Better test coverage
- Faster feedback
- Improved code quality

## 9. User Experience Improvements

### 9.1 Better Approval UI
**File**: `templates/ai_agent/approve_action.html`

**Description**: Improve approval UI with better organization.

**Implementation**:
- Add risk badge visualization
- Improve action card layout
- Add expandable details
- Add better visual hierarchy
- Improve button styling

**Benefits**:
- Better user experience
- Improved approval flow
- Better risk visibility

### 9.2 Real-time Updates
**File**: `templates/ai_agent/session_detail.html`

**Description**: Add real-time session updates.

**Implementation**:
- Add polling for session updates
- Update UI on each poll
- Show pending approvals
- Update attack tree visualization
- Add status indicators

**Benefits**:
- Better user experience
- Improved responsiveness
- Better session visibility

## 10. Performance Optimizations

### 10.1 Caching
**File**: `ai_agent/tools/caldera.py`

**Description**: Add caching for Caldera API calls.

**Implementation**:
- Add `@lru_cache` decorators
- Cache adversaries and planners
- Set appropriate cache sizes
- Add cache invalidation
- Add cache statistics

**Benefits**:
- Reduced API calls
- Better performance
- Lower latency

### 10.2 Async Improvements
**File**: `ai_agent/services/session_manager.py`

**Description**: Improve async operations with better error handling.

**Implementation**:
- Add timeout handling
- Implement better error recovery
- Add retry logic
- Improve error messages
- Add operation cancellation

**Benefits**:
- Better performance
- Improved reliability
- Better error handling

## Implementation Order

1. **Phase 1: Core Improvements** (High Priority)
   - 1.2 Better Failure Handling
   - 1.3 Adaptive Budget Allocation
   - 3.1 Better Operation Management
   - 3.2 Ability Execution with Retry
   - 6.1 Input Validation
   - 6.2 Rate Limiting
   - 6.3 Sanitization Improvements

2. **Phase 2: Context & State** (Medium Priority)
   - 2.1 Structured Context
   - 2.2 Context Compression
   - 2.3 VM-Specific Context
   - 4.1 State Aggregation
   - 4.2 State Querying

3. **Phase 3: Session Management** (Medium Priority)
   - 5.1 Session Templates
   - 5.2 Session Monitoring
   - 5.3 Auto-Resume

4. **Phase 4: Observability** (Low Priority)
   - 7.1 Structured Logging
   - 7.2 Metrics Collection

5. **Phase 5: Testing** (Low Priority)
   - 8.1 Mock Caldera Integration Tests
   - 8.2 Unit Tests for New Features

6. **Phase 6: UX & Performance** (Low Priority)
   - 9.1 Better Approval UI
   - 9.2 Real-time Updates
   - 10.1 Caching
   - 10.2 Async Improvements

## Testing Strategy

1. **Unit Tests**: Test each new feature in isolation
2. **Integration Tests**: Test with mocked Caldera
3. **Manual Testing**: Test with real sessions
4. **Performance Testing**: Measure improvements
5. **Security Testing**: Verify security improvements

## Success Metrics

1. **Performance**: Reduce API calls by 30%
2. **Reliability**: Increase operation success rate by 20%
3. **User Experience**: Reduce session setup time by 50%
4. **Security**: Reduce security incidents by 80%
5. **Test Coverage**: Increase test coverage to 80%

## Dependencies

- Python 3.12+
- httpx
- pytest
- prometheus-client (for metrics)
- No new external dependencies required

## Risk Assessment

1. **Low Risk**: Input validation, sanitization, caching
2. **Medium Risk**: Context compression, session templates
3. **High Risk**: Auto-resume, real-time updates

## Rollout Plan

1. **Phase 1**: Deploy to staging, test thoroughly
2. **Phase 2**: Deploy to production with monitoring
3. **Phase 3**: Gradual rollout based on feedback
4. **Phase 4**: Monitor and optimize
5. **Phase 5**: Full deployment

## Notes

- All changes should be backward compatible
- Add deprecation warnings for old features
- Document all changes in CLAUDE.md
- Update API documentation
- Add migration scripts if needed
