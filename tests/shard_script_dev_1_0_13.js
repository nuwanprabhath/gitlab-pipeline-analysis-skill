// Verbatim copy of paratoo-fdcp paratoo-webapp/scripts/cypress-parallel-offline.js at origin/dev/1.0.13 (last change ce089e96c2).
// hardcoding the jobs for offline

const NODE_INDEX = Number(process.env.CI_NODE_INDEX || 1)
const SPEC_GROUP = process.env.SPEC_GROUP || ''
const TEST_FOLDER = './test/cypress/integration/offline'

// Node 3's specs tolerate sharing a stack but not a job: some share a plot
// label and race on the same records if run concurrently. Grouped so specs
// sharing a label always land in the same group/job. Group names and
// membership must stay in sync with the `parallel: matrix` rows for
// cypress-offline-child-3 in .gitlab/cypress-offline-child.yml.
const NODE_3_SPEC_GROUPS = {
    qdaseq0009: [
        // QDASEQ0009
        'offline-acoustic.cy.js',
        'offline-partial-connection.cy.js',
        // Lays out SATRAFLB1000 (not QDASEQ0009 - see the comment on
        // plotContexts in the spec). Kept pinned in this group rather than
        // running concurrently because offline specs mutate a lot of shared
        // app/core state beyond plot labels.
        'offline-recruitment-survivorship.cy.js',
    ],
    satflb0003: [
        // SATFLB0003
        'offline-vertebrate.cy.js',
        'offline-herbarium-determination.cy.js',
    ],
    drones: [
        // SATRAFLB9001/2/3, unique to this spec
        'offline-drones.cy.js',
        // #2176: off-plot protocols only (Ground Counts Transects/Vantage Point,
        // Sign-based Vehicle Track) - no plot label, so no race with offline-drones.
        'offline-site-project-filter.cy.js',
    ],
    'camera-soils': [
        // offline-camera-trapping has no hardcoded plot labels;
        // offline-soils only uses SATFLB0001
        'offline-camera-trapping.cy.js',
        'offline-soils.cy.js',
    ],
}

// Order matters (setup, consumers, sync); names must stay in lockstep with the
// node-1 jobs in .gitlab/cypress-offline-child.yml.
const NODE_1_SPEC_GROUPS = {
    setup: ['offline-floristics-setup.cy.js'],
    recruitment: ['offline-floristics-consumers-recruitment.cy.js'],
    'survivorship-online': [
        'offline-floristics-consumers-survivorship-online.cy.js',
    ],
    'survivorship-offline': [
        'offline-floristics-consumers-survivorship-offline.cy.js',
    ],
    'condition-basal': ['offline-floristics-consumers-condition-basal.cy.js'],
    'cover-fire': ['offline-floristics-consumers-cover-fire.cy.js'],
    sync: ['offline-floristics-sync.cy.js'],
}

const GROUPS_BY_NODE = { 1: NODE_1_SPEC_GROUPS, 3: NODE_3_SPEC_GROUPS }

if (require.main === module) {
    // handle cli args: https://stackoverflow.com/a/24638042
    const argv = require('minimist')(process.argv.slice(2))
    if (argv?.['list-groups']) {
        console.log(Object.keys(GROUPS_BY_NODE[NODE_INDEX] || {}).join(','))
    } else if (argv?.stdout) {
        console.log(sortOfflineSpecs().join(','))
    }
}

module.exports = { NODE_1_SPEC_GROUPS, TEST_FOLDER }

// Empty group means the whole node (non-CI callers); unknown group throws.
function resolveGroup(table, group, nodeIndex) {
    const specs = group ? table[group] : Object.values(table).flat()
    if (!specs) {
        throw new Error(
            `Unknown SPEC_GROUP "${group}" for offline node ${nodeIndex}, ` +
                `expected one of: ${Object.keys(table).join(', ')}. Keep this ` +
                `table in sync with the node's jobs in .gitlab/cypress-offline-child.yml.`,
        )
    }
    return specs.map((f) => TEST_FOLDER + '/' + f)
}

// each case is a new job in the gitlab yml. need to edit that too. Look for offline-node
function sortOfflineSpecs() {
    switch (NODE_INDEX) {
        case 1:
            return resolveGroup(NODE_1_SPEC_GROUPS, SPEC_GROUP, 1)
        case 2:
            // plot-layout; pest fauna control is off-plot, so no label race
            return [
                TEST_FOLDER + '/offline-pest-fauna-control.cy.js',
                TEST_FOLDER + '/offline-plot-layout-visit-description.cy.js',
            ]
        case 3:
            return resolveGroup(NODE_3_SPEC_GROUPS, SPEC_GROUP, 3)
        case 4:
            // _summary-widgets.cy.js is not an offline spec. It is here for the
            // stack of its own: its Past Data "select every plot and visit" step
            // needs lists short enough for the virtual-scrolled QSelect to render in
            // full, and on the shared online DB other specs' plots and visits grew
            // them past that (nightly 2892684239, issue #2985). Cypress runs a job's
            // specs alphabetically, so the leading underscore keeps it ahead of the
            // floristics spec, on the fresh-seed snapshot. Node 4 because it is the
            // shortest offline node.
            return [
                TEST_FOLDER + '/_summary-widgets.cy.js',
                TEST_FOLDER + '/offline-floristics-and-dependencies-pro.cy.js',
            ]
        case 5:
            // Not an offline spec. It only needs a stack of its own.
            return ['./test/cypress/integration/priority/1_refresh-data.cy.js']
    }
}