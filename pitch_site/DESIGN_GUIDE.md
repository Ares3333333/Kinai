# Kinaesthetic AI · Design Guide (v1)

## Typography
- Base text: `16px`, line-height `1.5`
- Caption/meta: `12-13px`
- Card title: `11px` uppercase tracking
- KPI numbers: `42-70px` depending on container

## Spacing
- Global section gap: `18px`
- Inner card padding: `14-18px`
- Hero top spacing: `34px`
- Button gap: `10px`

## Components
- Card radius: `10-14px`
- Button height: `44px` (`56px` for primary start CTA)
- Border: `1px` with `--product-line`

## Runtime states
- `LIVE` = green (`--product-accent`)
- `DEMO` = amber (`--product-warn`)
- `OFFLINE/STALE` = red (`--product-danger`)

## Mandatory status surfaces
1. Runtime mode (`LIVE/DEMO/OFFLINE`)
2. Signal confidence
3. LLM health (`connected/timeout/fallback + latency`)
4. Last updated timestamp

## Investor mode
- One primary action on top row (`Demo lock 90s` or `Start`)
- Hide non-critical panels
- Show only: Tilt, Readiness, Coach Command, Recovery proof

## Accessibility
- Command area uses `role="status"` and `aria-live="polite"`
- Focus rings must stay visible on all buttons
- Keep text contrast AA+ on dark background
