<?php
/**
 * Plugin Name: AEGIS — AEO Optimizer
 * Plugin URI:  https://aegis-autopilot.com
 * Description: Audit any WordPress post for AI search optimization (AEO).
 *              Adds a sidebar panel that scores the editor draft against
 *              the AEGIS API and surfaces failed checks + a one-click
 *              "Open full report" link.
 * Version:     0.1.0
 * Author:      AEGIS
 * Author URI:  https://aegis-autopilot.com
 * License:     MIT
 * Requires PHP: 7.4
 * Requires at least: 6.0
 *
 * Configuration (Settings → AEGIS):
 *   - AEGIS_API_URL   default https://aegis-autopilot.com
 *   - AEGIS_API_KEY   org-scoped Bearer key, scope `audit:write`
 *
 * The plugin only talks to the AEGIS API; no third-party trackers.
 */

if (!defined('ABSPATH')) {
    exit;
}

define('AEGIS_PLUGIN_VERSION', '0.1.0');
define('AEGIS_DEFAULT_API_URL', 'https://aegis-autopilot.com');

// ---------------------------------------------------------------------------
// Settings page
// ---------------------------------------------------------------------------

add_action('admin_menu', function () {
    add_options_page(
        'AEGIS Settings',
        'AEGIS',
        'manage_options',
        'aegis-settings',
        'aegis_render_settings_page'
    );
});

add_action('admin_init', function () {
    register_setting('aegis_settings_group', 'aegis_api_url', [
        'type' => 'string',
        'sanitize_callback' => 'esc_url_raw',
        'default' => AEGIS_DEFAULT_API_URL,
    ]);
    register_setting('aegis_settings_group', 'aegis_api_key', [
        'type' => 'string',
        'sanitize_callback' => 'sanitize_text_field',
        'default' => '',
    ]);
    register_setting('aegis_settings_group', 'aegis_threshold', [
        'type' => 'integer',
        'sanitize_callback' => 'absint',
        'default' => 65,
    ]);
});

function aegis_render_settings_page() {
    if (!current_user_can('manage_options')) {
        return;
    }
    ?>
    <div class="wrap">
        <h1>AEGIS — AEO Optimizer</h1>
        <form method="post" action="options.php">
            <?php settings_fields('aegis_settings_group'); ?>
            <table class="form-table">
                <tr>
                    <th><label for="aegis_api_url">API base URL</label></th>
                    <td>
                        <input type="url" name="aegis_api_url" id="aegis_api_url"
                            class="regular-text"
                            value="<?php echo esc_attr(get_option('aegis_api_url', AEGIS_DEFAULT_API_URL)); ?>" />
                        <p class="description">Defaults to <code><?php echo esc_html(AEGIS_DEFAULT_API_URL); ?></code>. Override only for self-hosted installs.</p>
                    </td>
                </tr>
                <tr>
                    <th><label for="aegis_api_key">API key</label></th>
                    <td>
                        <input type="password" name="aegis_api_key" id="aegis_api_key"
                            class="regular-text" autocomplete="off"
                            value="<?php echo esc_attr(get_option('aegis_api_key', '')); ?>" />
                        <p class="description">Org-scoped Bearer key with the <code>audit:write</code> scope. Mint at <em>aegis-autopilot.com → Org → API keys</em>.</p>
                    </td>
                </tr>
                <tr>
                    <th><label for="aegis_threshold">Pass threshold</label></th>
                    <td>
                        <input type="number" name="aegis_threshold" id="aegis_threshold"
                            min="0" max="100" step="1"
                            value="<?php echo esc_attr(get_option('aegis_threshold', 65)); ?>" />
                        <p class="description">Below this AEO score the sidebar shows a red badge.</p>
                    </td>
                </tr>
            </table>
            <?php submit_button(); ?>
        </form>
    </div>
    <?php
}

// ---------------------------------------------------------------------------
// Sidebar editor JS
// ---------------------------------------------------------------------------

add_action('enqueue_block_editor_assets', function () {
    $plugin_url = plugin_dir_url(__FILE__);
    wp_register_script(
        'aegis-editor',
        $plugin_url . 'aegis-editor.js',
        ['wp-plugins', 'wp-edit-post', 'wp-element', 'wp-components', 'wp-data', 'wp-i18n'],
        AEGIS_PLUGIN_VERSION,
        true
    );
    wp_localize_script('aegis-editor', 'AEGIS_CONFIG', [
        'restUrl'   => rest_url('aegis/v1/audit'),
        'restNonce' => wp_create_nonce('wp_rest'),
        'threshold' => (int) get_option('aegis_threshold', 65),
    ]);
    wp_enqueue_script('aegis-editor');
});

// ---------------------------------------------------------------------------
// REST proxy — the editor calls this, the plugin forwards to AEGIS.
// Hiding the API key server-side keeps it out of the browser bundle.
// ---------------------------------------------------------------------------

add_action('rest_api_init', function () {
    register_rest_route('aegis/v1', '/audit', [
        'methods' => 'POST',
        'permission_callback' => function () {
            return current_user_can('edit_posts');
        },
        'callback' => 'aegis_rest_audit',
        'args' => [
            'html' => [
                'required' => true,
                'type' => 'string',
            ],
        ],
    ]);
});

function aegis_rest_audit(WP_REST_Request $request) {
    $html = (string) $request->get_param('html');
    if (trim($html) === '') {
        return new WP_Error('aegis_empty', 'Post content is empty.', ['status' => 400]);
    }

    $api_url = rtrim((string) get_option('aegis_api_url', AEGIS_DEFAULT_API_URL), '/');
    $api_key = (string) get_option('aegis_api_key', '');
    if ($api_key === '') {
        return new WP_Error(
            'aegis_unconfigured',
            'Configure your AEGIS API key under Settings → AEGIS first.',
            ['status' => 503]
        );
    }

    $response = wp_remote_post(
        $api_url . '/api/v1/audit',
        [
            'timeout' => 30,
            'headers' => [
                'Content-Type'  => 'application/json',
                'Authorization' => 'Bearer ' . $api_key,
                'User-Agent'    => 'AEGIS-WordPress/' . AEGIS_PLUGIN_VERSION,
            ],
            'body' => wp_json_encode([
                'input_type'  => 'text',
                'input_value' => $html,
            ]),
        ]
    );

    if (is_wp_error($response)) {
        return new WP_Error(
            'aegis_network',
            'Could not reach AEGIS API: ' . $response->get_error_message(),
            ['status' => 502]
        );
    }
    $code = wp_remote_retrieve_response_code($response);
    $body = wp_remote_retrieve_body($response);
    $data = json_decode($body, true);
    if ($code >= 400 || !is_array($data)) {
        return new WP_Error(
            'aegis_upstream',
            'AEGIS returned HTTP ' . $code . ': ' . substr((string) $body, 0, 200),
            ['status' => 502]
        );
    }
    return rest_ensure_response($data);
}
