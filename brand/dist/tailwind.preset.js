// Сгенерировано brand/build.py из brand/tokens.json. Не редактируйте вручную
// Пресет Tailwind CSS v3: подключите brand/dist/tokens.css и укажите
// presets: [require('./brand/dist/tailwind.preset.js')] в tailwind.config.js
module.exports = {
  "theme": {
    "screens": {
      "sm": "430px",
      "md": "768px",
      "lg": "1280px",
      "xl": "1440px"
    },
    "extend": {
      "colors": {
        "fsp": {
          "bg": "color-mix(in srgb, var(--fsp-color-bg) calc(<alpha-value> * 100%), transparent)",
          "surface": "color-mix(in srgb, var(--fsp-color-surface) calc(<alpha-value> * 100%), transparent)",
          "surface-muted": "color-mix(in srgb, var(--fsp-color-surface-muted) calc(<alpha-value> * 100%), transparent)",
          "surface-hover": "color-mix(in srgb, var(--fsp-color-surface-hover) calc(<alpha-value> * 100%), transparent)",
          "border": "color-mix(in srgb, var(--fsp-color-border) calc(<alpha-value> * 100%), transparent)",
          "border-strong": "color-mix(in srgb, var(--fsp-color-border-strong) calc(<alpha-value> * 100%), transparent)",
          "text": "color-mix(in srgb, var(--fsp-color-text) calc(<alpha-value> * 100%), transparent)",
          "text-muted": "color-mix(in srgb, var(--fsp-color-text-muted) calc(<alpha-value> * 100%), transparent)",
          "text-on-primary": "color-mix(in srgb, var(--fsp-color-text-on-primary) calc(<alpha-value> * 100%), transparent)",
          "primary": "color-mix(in srgb, var(--fsp-color-primary) calc(<alpha-value> * 100%), transparent)",
          "primary-hover": "color-mix(in srgb, var(--fsp-color-primary-hover) calc(<alpha-value> * 100%), transparent)",
          "primary-active": "color-mix(in srgb, var(--fsp-color-primary-active) calc(<alpha-value> * 100%), transparent)",
          "primary-soft": "color-mix(in srgb, var(--fsp-color-primary-soft) calc(<alpha-value> * 100%), transparent)",
          "link": "color-mix(in srgb, var(--fsp-color-link) calc(<alpha-value> * 100%), transparent)",
          "link-hover": "color-mix(in srgb, var(--fsp-color-link-hover) calc(<alpha-value> * 100%), transparent)",
          "accent": "color-mix(in srgb, var(--fsp-color-accent) calc(<alpha-value> * 100%), transparent)",
          "danger": "color-mix(in srgb, var(--fsp-color-danger) calc(<alpha-value> * 100%), transparent)",
          "danger-hover": "color-mix(in srgb, var(--fsp-color-danger-hover) calc(<alpha-value> * 100%), transparent)",
          "danger-soft": "color-mix(in srgb, var(--fsp-color-danger-soft) calc(<alpha-value> * 100%), transparent)",
          "danger-text": "color-mix(in srgb, var(--fsp-color-danger-text) calc(<alpha-value> * 100%), transparent)",
          "neutral-soft": "color-mix(in srgb, var(--fsp-color-neutral-soft) calc(<alpha-value> * 100%), transparent)",
          "control": "color-mix(in srgb, var(--fsp-color-control) calc(<alpha-value> * 100%), transparent)",
          "control-knob": "color-mix(in srgb, var(--fsp-color-control-knob) calc(<alpha-value> * 100%), transparent)",
          "focus": "color-mix(in srgb, var(--fsp-color-focus) calc(<alpha-value> * 100%), transparent)",
          "shadow": "color-mix(in srgb, var(--fsp-color-shadow) calc(<alpha-value> * 100%), transparent)",
          "overlay": "color-mix(in srgb, var(--fsp-color-overlay) calc(<alpha-value> * 100%), transparent)",
          "selection": "color-mix(in srgb, var(--fsp-color-selection) calc(<alpha-value> * 100%), transparent)",
          "palette": {
            "blue": "color-mix(in srgb, var(--fsp-palette-blue) calc(<alpha-value> * 100%), transparent)",
            "red": "color-mix(in srgb, var(--fsp-palette-red) calc(<alpha-value> * 100%), transparent)",
            "light": "color-mix(in srgb, var(--fsp-palette-light) calc(<alpha-value> * 100%), transparent)",
            "gray": "color-mix(in srgb, var(--fsp-palette-gray) calc(<alpha-value> * 100%), transparent)",
            "dark": "color-mix(in srgb, var(--fsp-palette-dark) calc(<alpha-value> * 100%), transparent)",
            "gray-bright": "color-mix(in srgb, var(--fsp-palette-gray-bright) calc(<alpha-value> * 100%), transparent)",
            "gray-middle": "color-mix(in srgb, var(--fsp-palette-gray-middle) calc(<alpha-value> * 100%), transparent)",
            "gray-light-2": "color-mix(in srgb, var(--fsp-palette-gray-light-2) calc(<alpha-value> * 100%), transparent)",
            "gray-light": "color-mix(in srgb, var(--fsp-palette-gray-light) calc(<alpha-value> * 100%), transparent)",
            "digital": "color-mix(in srgb, var(--fsp-palette-digital) calc(<alpha-value> * 100%), transparent)",
            "white": "color-mix(in srgb, var(--fsp-palette-white) calc(<alpha-value> * 100%), transparent)"
          }
        }
      },
      "fontFamily": {
        "brand": "var(--fsp-font-family-brand)",
        "text": "var(--fsp-font-family-text)"
      },
      "fontWeight": {
        "regular": "var(--fsp-font-weight-regular)",
        "medium": "var(--fsp-font-weight-medium)",
        "bold": "var(--fsp-font-weight-bold)"
      },
      "fontSize": {
        "xs": [
          "var(--fsp-text-xs-size)",
          {
            "lineHeight": "var(--fsp-text-xs-leading)"
          }
        ],
        "sm": [
          "var(--fsp-text-sm-size)",
          {
            "lineHeight": "var(--fsp-text-sm-leading)"
          }
        ],
        "md": [
          "var(--fsp-text-md-size)",
          {
            "lineHeight": "var(--fsp-text-md-leading)"
          }
        ],
        "lg": [
          "var(--fsp-text-lg-size)",
          {
            "lineHeight": "var(--fsp-text-lg-leading)"
          }
        ],
        "xl": [
          "var(--fsp-text-xl-size)",
          {
            "lineHeight": "var(--fsp-text-xl-leading)"
          }
        ],
        "2xl": [
          "var(--fsp-text-2xl-size)",
          {
            "lineHeight": "var(--fsp-text-2xl-leading)"
          }
        ],
        "3xl": [
          "var(--fsp-text-3xl-size)",
          {
            "lineHeight": "var(--fsp-text-3xl-leading)"
          }
        ],
        "4xl": [
          "var(--fsp-text-4xl-size)",
          {
            "lineHeight": "var(--fsp-text-4xl-leading)"
          }
        ]
      },
      "spacing": {
        "0": "var(--fsp-space-0)",
        "1": "var(--fsp-space-1)",
        "2": "var(--fsp-space-2)",
        "3": "var(--fsp-space-3)",
        "4": "var(--fsp-space-4)",
        "5": "var(--fsp-space-5)",
        "6": "var(--fsp-space-6)",
        "8": "var(--fsp-space-8)",
        "10": "var(--fsp-space-10)",
        "12": "var(--fsp-space-12)",
        "16": "var(--fsp-space-16)"
      },
      "height": {
        "control-sm": "var(--fsp-control-sm)",
        "control-md": "var(--fsp-control-md)",
        "control-lg": "var(--fsp-control-lg)"
      },
      "minHeight": {
        "control-sm": "var(--fsp-control-sm)",
        "control-md": "var(--fsp-control-md)",
        "control-lg": "var(--fsp-control-lg)"
      },
      "borderRadius": {
        "sm": "var(--fsp-radius-sm)",
        "md": "var(--fsp-radius-md)",
        "lg": "var(--fsp-radius-lg)",
        "pill": "var(--fsp-radius-pill)"
      },
      "boxShadow": {
        "sm": "var(--fsp-shadow-sm)",
        "md": "var(--fsp-shadow-md)",
        "lg": "var(--fsp-shadow-lg)"
      },
      "transitionDuration": {
        "fast": "var(--fsp-duration-fast)",
        "base": "var(--fsp-duration-base)",
        "slow": "var(--fsp-duration-slow)"
      },
      "transitionTimingFunction": {
        "fsp-standard": "var(--fsp-easing-standard)"
      }
    }
  }
};
