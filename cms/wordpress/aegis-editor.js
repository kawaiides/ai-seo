/**
 * AEGIS editor sidebar.
 *
 * Adds a "AEGIS AEO" panel to the Gutenberg post sidebar with:
 *   - Big number: the current AEO score (0–100).
 *   - Coloured band (green / amber / red) keyed off the configured threshold.
 *   - Per-check pass/fail rows so the writer sees exactly which check
 *     dropped the score.
 *   - "Audit now" button — POSTs the current post content to our PHP
 *     REST proxy, which forwards to `/api/v1/audit`.
 *
 * The audit isn't auto-triggered on every keystroke to keep API spend
 * predictable; writers click "Audit now" when they want a fresh read.
 */

(function (wp) {
    if (!wp || !wp.plugins || !wp.editPost) {
        return;
    }
    const { registerPlugin } = wp.plugins;
    const { PluginSidebar, PluginSidebarMoreMenuItem } = wp.editPost;
    const { PanelBody, Button, Spinner } = wp.components;
    const { useState, useCallback, createElement: el, Fragment } = wp.element;
    const { useSelect } = wp.data;
    const { __ } = wp.i18n;

    const threshold = (window.AEGIS_CONFIG && window.AEGIS_CONFIG.threshold) || 65;

    function bandColor(score) {
        if (score >= 85) return '#16a34a';
        if (score >= threshold) return '#65a30d';
        if (score >= 40) return '#d97706';
        return '#dc2626';
    }

    function bandLabel(score) {
        if (score >= 85) return __('AEO Optimized', 'aegis');
        if (score >= 65) return __('Needs Improvement', 'aegis');
        if (score >= 40) return __('Significant Gaps', 'aegis');
        return __('Not AEO Ready', 'aegis');
    }

    function AegisPanel() {
        const [state, setState] = useState({
            status: 'idle',
            result: null,
            error: null,
        });

        const postContent = useSelect((select) => {
            return select('core/editor').getEditedPostContent();
        }, []);

        const runAudit = useCallback(async () => {
            setState({ status: 'loading', result: null, error: null });
            try {
                const resp = await fetch(window.AEGIS_CONFIG.restUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-WP-Nonce': window.AEGIS_CONFIG.restNonce,
                    },
                    body: JSON.stringify({ html: postContent }),
                });
                const data = await resp.json();
                if (!resp.ok) {
                    setState({
                        status: 'error',
                        result: null,
                        error: data && data.message ? data.message : 'Audit failed',
                    });
                    return;
                }
                setState({ status: 'ready', result: data, error: null });
            } catch (e) {
                setState({ status: 'error', result: null, error: e.message });
            }
        }, [postContent]);

        const panelChildren = [];

        if (state.status === 'idle') {
            panelChildren.push(
                el(
                    'p',
                    { key: 'tip' },
                    __('Click "Audit now" to score this draft against the AEGIS AEO checks.', 'aegis')
                )
            );
        }

        if (state.status === 'loading') {
            panelChildren.push(el(Spinner, { key: 'spin' }));
        }

        if (state.status === 'error' && state.error) {
            panelChildren.push(
                el(
                    'p',
                    { key: 'err', style: { color: '#dc2626' } },
                    state.error
                )
            );
        }

        if (state.status === 'ready' && state.result) {
            const r = state.result;
            panelChildren.push(
                el(
                    'div',
                    {
                        key: 'score',
                        style: {
                            fontSize: '40px',
                            fontWeight: 700,
                            color: bandColor(r.aeo_score),
                            lineHeight: 1.1,
                        },
                    },
                    r.aeo_score
                )
            );
            panelChildren.push(
                el(
                    'div',
                    { key: 'band', style: { fontSize: '13px', marginBottom: '12px' } },
                    bandLabel(r.aeo_score) + (r.plan === 'pro' ? '' : ' · Free plan')
                )
            );
            if (Array.isArray(r.checks)) {
                panelChildren.push(
                    el(
                        'ul',
                        {
                            key: 'checks',
                            style: {
                                listStyle: 'none',
                                padding: 0,
                                margin: 0,
                                fontSize: '12px',
                            },
                        },
                        r.checks.map((c, i) =>
                            el(
                                'li',
                                {
                                    key: 'c' + i,
                                    style: {
                                        padding: '4px 0',
                                        borderTop: i === 0 ? 'none' : '1px solid #eee',
                                        color: c.passed ? '#15803d' : '#b91c1c',
                                    },
                                },
                                (c.passed ? '✓ ' : '✗ ') + c.name + ' (' + c.score + '/' + c.max_score + ')'
                            )
                        )
                    )
                );
            }
            if (Array.isArray(r.locked_checks) && r.locked_checks.length) {
                panelChildren.push(
                    el(
                        'div',
                        {
                            key: 'locked',
                            style: {
                                marginTop: '10px',
                                padding: '8px',
                                background: '#f5f5f5',
                                fontSize: '11px',
                            },
                        },
                        __('Pro checks (upgrade to unlock): ', 'aegis') +
                            r.locked_checks.map((l) => l.name).join(', ')
                    )
                );
            }
        }

        panelChildren.push(
            el(
                Button,
                {
                    key: 'btn',
                    variant: 'primary',
                    onClick: runAudit,
                    disabled: state.status === 'loading' || !postContent,
                    style: { marginTop: '12px' },
                },
                state.status === 'loading'
                    ? __('Auditing…', 'aegis')
                    : __('Audit now', 'aegis')
            )
        );

        return el(PanelBody, { title: __('AEGIS AEO', 'aegis'), initialOpen: true }, panelChildren);
    }

    function AegisSidebar() {
        return el(
            Fragment,
            {},
            el(
                PluginSidebarMoreMenuItem,
                { target: 'aegis-sidebar', icon: 'chart-bar' },
                __('AEGIS AEO', 'aegis')
            ),
            el(
                PluginSidebar,
                { name: 'aegis-sidebar', title: __('AEGIS AEO', 'aegis'), icon: 'chart-bar' },
                el(AegisPanel)
            )
        );
    }

    registerPlugin('aegis-aeo', { render: AegisSidebar, icon: 'chart-bar' });
})(window.wp);
