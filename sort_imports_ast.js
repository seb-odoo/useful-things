// npm install glob recast @babel/parser
const fs = require("fs");
const { glob } = require("glob");
const recast = require("recast");
const { builders, namedTypes } = require("ast-types");

const { visit } = recast.types;
const babelParser = require("recast/parsers/babel");

// Function to transform relative import paths to absolute import paths
function transformImportPath(importPath, moduleName, filePath) {
    let transformedPath = importPath;

    // Resolve the directory of the current file
    const fileDir = filePath.substring(0, filePath.lastIndexOf("/"));

    // Replace './' and '../' with absolute paths
    if (importPath.startsWith("./") || importPath.startsWith("../")) {
        const absolutePath = require("path").resolve(fileDir, importPath);
        transformedPath = "@" + moduleName + "/" + absolutePath.replace(/^.*addons\/[^/]+\//, "");
    }

    // Transform 'static/tests/' into '../'
    transformedPath = transformedPath.replace(/static\/tests\//, "../");
    // Remove 'static/src/' prefix
    transformedPath = transformedPath.replace(/static\/src\//, "");
    // Remove the final '.js'
    transformedPath = transformedPath.replace(/\.js$/, "");

    return transformedPath;
}

// Function to process a single file
async function processFile(filePath) {
    console.log(`Processing file: ${filePath}`);
    const content = fs.readFileSync(filePath, "utf-8");
    const ast = recast.parse(content, { parser: babelParser });

    // Find the module name (the directory just after 'addons/')
    const moduleNameMatch = filePath.match(/addons\/([^/]+)\//);
    const moduleName = moduleNameMatch ? moduleNameMatch[1] : "";

    // Transform relative import paths to absolute import paths
    visit(ast, {
        visitImportDeclaration(path) {
            const source = path.node.source.value;
            if (source.startsWith(".") || source.startsWith("../")) {
                path.node.source.value = transformImportPath(source, moduleName, filePath);
            }
            this.traverse(path);
        },
    });

    const body = ast.program.body;

    // Separate import statements from other statements
    const importStatements = [];
    const otherStatements = [];
    body.forEach((node) => {
        if (namedTypes.ImportDeclaration.check(node)) {
            importStatements.push(node);
        } else {
            otherStatements.push(node);
        }
    });

    // Sort import statements by source value
    importStatements.sort((a, b) => {
        const sourceA = a.source.value;
        const sourceB = b.source.value;
        return sourceA.localeCompare(sourceB);
    });

    // Group imports by prefix '@word/'
    const groupedImports = [];
    let previousPrefix = null;
    importStatements.forEach((statement) => {
        const source = statement.source.value;
        const prefixMatch = source.match(/^@([^/]+)\//);
        const currentPrefix = prefixMatch ? prefixMatch[0] : null;
        if (previousPrefix && previousPrefix !== currentPrefix) {
            groupedImports.push(
                builders.expressionStatement(builders.identifier("/*__EMPTY_LINE__*/"))
            );
        }
        groupedImports.push(statement);
        previousPrefix = currentPrefix;
    });
    if (importStatements.length > 0) {
        groupedImports.push(
            builders.expressionStatement(builders.identifier("/*__EMPTY_LINE__*/"))
        );
    }

    // Combine sorted imports and other statements
    ast.program.body = otherStatements;
    const otherStatementsContent = recast.print(ast).code;
    ast.program.body = groupedImports;
    const newImportContent = recast.print(ast, { reuseWhitespace: false }).code;
    const newContent = (newImportContent + otherStatementsContent).replace(
        /\n\s*\/\*__EMPTY_LINE__\*\/;\n\n?/g,
        "\n\n"
    );
    if (newContent !== content) {
        fs.writeFileSync(filePath, newContent, "utf-8");
        console.log(`File ${filePath} has been updated.`);
    }
}

// Process all .js files in the ../odoo/addons/mail directory
async function processAllFiles() {
    try {
        const jsFiles = await glob("../odoo/addons/mail/**/*.js", {
            ignore: [
                "../odoo/addons/mail/static/lib/**",
                "../odoo/addons/mail/push-to-talk-extension/**",
            ],
        });
        console.log(`Found ${jsFiles.length} files.`);
        for (const filePath of jsFiles) {
            await processFile(filePath);
        }
    } catch (err) {
        console.error("Error finding .js files:", err);
    }
}

processAllFiles();
