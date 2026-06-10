content = open('frontend/src/index.css', 'r', encoding='utf-8').read()

# Insert new FAB CSS right after the .bottom-nav-item:active rule
old_marker = '  .bottom-nav-item:active { background: #f0fdf4; }\n'
new_css = '''  .bottom-nav-item:active { background: #f0fdf4; }

  /* ================================================================
   * FLOATING ACTION BUTTONS (FAB) - Map Toggle & Cart
   * Always float above the bottom nav bar, safe on all devices.
   * Base clearance: 72px (bottom-nav) + env(safe-area-inset-bottom)
   * ================================================================ */

  /* CSS custom property for floating button safe bottom offset */
  :root {
    --fab-bottom-base: calc(72px + env(safe-area-inset-bottom));
    --fab-bottom-primary: calc(var(--fab-bottom-base) + 16px);   /* map toggle */
    --fab-bottom-secondary: calc(var(--fab-bottom-base) + 88px); /* cart, stacked above toggle */
  }

  /* Map / List toggle FAB */
  .map-fab-toggle {
    display: inline-flex;
    align-items: center;
    gap: 9px;
    background: #111827;
    color: #fff;
    font-size: 14px;
    font-weight: 700;
    padding: 13px 22px;
    border-radius: 999px;
    /* Minimum touch target: 48px height */
    min-height: 48px;
    min-width: 48px;
    /* Layered shadow so it reads as an elevated action button */
    box-shadow:
      0 2px 4px rgba(0,0,0,0.10),
      0 8px 24px rgba(0,0,0,0.20),
      0 20px 48px rgba(0,0,0,0.14);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    touch-action: manipulation;
    user-select: none;
    transition: background 0.18s ease, box-shadow 0.18s ease;
    white-space: nowrap;
  }
  .map-fab-toggle:hover {
    background: #1f2937;
    box-shadow:
      0 4px 8px rgba(0,0,0,0.12),
      0 14px 32px rgba(0,0,0,0.24),
      0 28px 56px rgba(0,0,0,0.16);
  }
  .map-fab-toggle:active {
    background: #374151;
  }

  /* First-load pulse animation for map toggle */
  @keyframes fab-pulse-once {
    0%   { box-shadow: 0 0 0 0   rgba(17,24,39,0.45), 0 8px 24px rgba(0,0,0,0.20); }
    60%  { box-shadow: 0 0 0 14px rgba(17,24,39,0.00), 0 8px 24px rgba(0,0,0,0.20); }
    100% { box-shadow: 0 0 0 0   rgba(17,24,39,0.00), 0 8px 24px rgba(0,0,0,0.20); }
  }
  .map-fab-toggle {
    animation: fab-pulse-once 0.8s ease-out 0.5s 1 both;
  }

  /* Cart FAB pulse */
  @keyframes cart-fab-pulse {
    0%, 100% { transform: scale(1)   translateY(0);   }
    30%       { transform: scale(1.1) translateY(-3px); }
    60%       { transform: scale(0.96) translateY(1px); }
  }
  .cart-fab-pulse {
    animation: cart-fab-pulse 2.4s ease-in-out infinite;
  }

  /* Landscape mode: tighten bottom offset slightly */
  @media (max-height: 500px) and (orientation: landscape) {
    :root {
      --fab-bottom-base: calc(0px + env(safe-area-inset-bottom));
      --fab-bottom-primary: calc(var(--fab-bottom-base) + 12px);
    }
    /* Hide bottom nav in landscape (it stacks content); FABs float from bottom */
    .bottom-nav { display: none; }
  }

  /* Desktop: hide mobile FABs entirely */
  @media (min-width: 1024px) {
    .map-fab-toggle { display: none; }
    .cart-fab-pulse { /* no adjustment needed on desktop */ }
  }

'''

if old_marker in content:
    content = content.replace(old_marker, new_css, 1)
    print('CSS inserted!')
else:
    print('Marker NOT FOUND')

open('frontend/src/index.css', 'w', encoding='utf-8').write(content)
