// The documentation site, published to GitHub Pages by .github/workflows/docs.yml.
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

export default defineConfig({
  site: 'https://anthonypluth.github.io',
  base: '/Runway',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'Runway',
      description: 'A self-hosted personal finance app that tells you how much runway your cash has.',
      logo: { src: './src/assets/logo.svg' },
      favicon: '/favicon.svg',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/AnthonyPluth/Runway' }],
      editLink: { baseUrl: 'https://github.com/AnthonyPluth/Runway/edit/main/docs/' },
      customCss: ['./src/styles/custom.css'],
      lastUpdated: true,
      // A broken link between pages fails the build (and so the pull request).
      plugins: [starlightLinksValidator()],
      sidebar: [
        { label: 'Start here', items: [{ autogenerate: { directory: 'start' } }] },
        { label: 'Using Runway', items: [{ autogenerate: { directory: 'using' } }] },
        { label: 'Reference', items: [{ autogenerate: { directory: 'reference' } }] },
        { label: 'Contributing', items: [{ autogenerate: { directory: 'contributing' } }] },
      ],
    }),
  ],
});
