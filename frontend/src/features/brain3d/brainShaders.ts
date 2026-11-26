import * as THREE from "three";

export function createSynapseEdgeMaterial(isDark: boolean): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uBaseOpacity: { value: isDark ? 0.34 : 0.28 },
      uGlowStrength: { value: isDark ? 1.35 : 0.95 },
    },
    vertexShader: `
      attribute float aPulse;
      attribute float aAlpha;
      varying vec3 vColor;
      varying float vPulse;
      varying float vAlpha;
      void main() {
        vColor = color;
        vPulse = aPulse;
        vAlpha = aAlpha;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform float uBaseOpacity;
      uniform float uGlowStrength;
      varying vec3 vColor;
      varying float vPulse;
      varying float vAlpha;
      void main() {
        float wave = 0.65 + 0.35 * sin(uTime * 3.8 + vPulse * 6.2831);
        float boost = mix(1.0, uGlowStrength * (1.2 + 0.5 * wave), vPulse);
        float alpha = clamp(uBaseOpacity * vAlpha * mix(1.0, 2.4, vPulse), 0.03, 0.96);
        gl_FragColor = vec4(vColor * boost, alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    vertexColors: true,
    blending: isDark ? THREE.AdditiveBlending : THREE.NormalBlending,
  });
}

export function createSparkMaterial(isDark: boolean): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uSize: { value: isDark ? 18.0 : 14.0 },
    },
    vertexShader: `
      uniform float uSize;
      attribute float aIntensity;
      varying vec3 vColor;
      varying float vIntensity;
      void main() {
        vColor = color;
        vIntensity = aIntensity;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = uSize * (0.65 + aIntensity * 0.9) * (3.2 / max(1.2, -mvPosition.z));
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      varying float vIntensity;
      void main() {
        vec2 centered = gl_PointCoord - vec2(0.5);
        float dist = length(centered);
        if (dist > 0.5) discard;
        float core = exp(-dist * dist * 14.0);
        float halo = smoothstep(0.5, 0.0, dist) * 0.65;
        float alpha = clamp((core + halo) * vIntensity, 0.0, 1.0);
        gl_FragColor = vec4(vColor * (1.1 + core * 0.9), alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    vertexColors: true,
    blending: THREE.AdditiveBlending,
  });
}

export function createDustMaterial(isDark: boolean): THREE.PointsMaterial {
  return new THREE.PointsMaterial({
    size: 0.045,
    color: isDark ? 0x67e8f9 : 0x3b82f6,
    transparent: true,
    opacity: isDark ? 0.32 : 0.22,
    depthWrite: false,
    blending: isDark ? THREE.AdditiveBlending : THREE.NormalBlending,
  });
}
