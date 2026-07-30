# Hermes Model Routing Policy

This file documents the cost-aware routing tiers used by the model-router plugin.
Routing is AUTOMATIC -- the plugin classifies each turn by complexity and switches
the model before every LLM call. No manual /model switching needed.

Plugin location: active Hermes home `plugins/model-router`
Router config:    active Hermes home `model_router.yaml`
Classifier:       deepseek / deepseek-v4-flash

## Goals

- Default to the cheapest model that can likely complete the task well.
- Escalate only when the task actually needs deeper reasoning or better context handling.
- Keep expensive Sonnet usage rare and intentional.

## Tier Table

Tier 1
  Label:     T1 Flash
  Model:     deepseek-v4-flash
  Reasoning: none
  Role:      fast triage and cheap helper
  Triggers:
    - Short acknowledgements
    - Intent classification
    - Status checks
    - Title generation
    - Cron job output processing

Tier 2
  Label:     T2 Flash Daily
  Model:     deepseek-v4-flash
  Reasoning: none
  Role:      default daily-driver
  Triggers:
    - Default day-to-day work
    - Documentation and drafting
    - Standard coding and research
    - Routine file operations

Tier 3
  Label:     T3 Pro Lite
  Model:     deepseek-v4-pro
  Reasoning: low
  Role:      basic coding, creating, review
  Triggers:
    - Debugging
    - Code review
    - Large-document synthesis
    - Complex analysis
    - Multi-file refactoring

Tier 4
  Label:     T4 Pro Reasoning
  Model:     deepseek-v4-pro
  Reasoning: high
  Role:      strong reasoning and planning
  Triggers:
    - Architecture design
    - Migration planning
    - Complex multi-step design
    - Nuanced code review
    - Security analysis

Tier 5
  Label:     T5 Pro Deep Think
  Model:     deepseek-v4-pro
  Reasoning: xhigh
  Role:      expensive deep-think mode
  Triggers:
    - Algorithmic optimization
    - High-stakes reasoning
    - Complex debugging with many variables
    - Financial modelling

## Escalation Rules

1. Start with Tier 2 for most normal user work.
2. Use Tier 1 for ultra-short acks, triage, or mechanical helper tasks.
3. Escalate to Tier 3 when debugging, reviewing, or handling large docs.
4. Escalate to Tier 4 for design, planning, or architecture work.
5. Escalate to Tier 5 only for security-critical or algorithmically dense tasks.
6. Fail fast: if unsure, pick the cheaper tier first.

## Cost Notes

- Prompt caching should remain enabled.
- Keep cheap models on auxiliary tasks.
- Avoid using Sonnet for rote edits, summaries, or shell/file boilerplate.
