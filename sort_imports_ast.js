// npm install glob recast @babel/parser
const fs = require("fs");
const { glob } = require("glob");
const recast = require("recast");
const { namedTypes: n, builders: b } = require("ast-types");

const { visit } = recast.types;
const babelParser = require("recast/parsers/babel");

// Function to transform relative import paths to absolute import paths
function transformImportPath(importPath, moduleName) {
    let transformedPath = importPath;
    // Add '@' and module name at the start if the path starts with ./
    if (importPath.startsWith("./")) {
        transformedPath = '@' + moduleName + '/' + transformedPath;
    }
    // Transform 'static/tests/' into '../'
    transformedPath = transformedPath.replace(/^static\/tests\//, '../');
    // Remove the final '.js'
    transformedPath = transformedPath.replace(/\.js$/, '');
    return transformedPath;
}

// Function to process a single file
async function processFile(filePath) {
    console.log(`Processing file: ${filePath}`);
    const content = fs.readFileSync(filePath, "utf-8");
    const ast = recast.parse(content, { parser: babelParser });

    // Find the module name (the directory just after 'addons/')
    const moduleNameMatch = filePath.match(/addons\/([^/]+)\//);
    const moduleName = moduleNameMatch ? moduleNameMatch[1] : '';
    console.log(`Module name: ${moduleName}`);

    // Remove empty statements between two import statements
    visit(ast, {
        visitProgram(path) {
            const body = path.get("body");
            let lastWasImport = false;
            for (let i = body.value.length - 1; i >= 0; i--) {
                const node = body.get(i);
                if (n.ImportDeclaration.check(node.value)) {
                    lastWasImport = true;
                } else if (lastWasImport && node.value.type === "EmptyStatement") {
                    path.get("body", i).prune();
                } else {
                    lastWasImport = false;
                }
            }
            this.traverse(path);
        }
    });

    // Transform relative import paths to absolute import paths
    visit(ast, {
        visitImportDeclaration(path) {
            const source = path.node.source.value;
            if (source.startsWith(".")) {
                path.node.source.value = transformImportPath(source, moduleName);
            }
            this.traverse(path);
        }
    });

    const body = ast.program.body;

    // Separate import statements from other statements
    const importStatements = [];
    const otherStatements = [];
    body.forEach(node => {
        if (n.ImportDeclaration.check(node)) {
            importStatements.push(node);
        } else {
            otherStatements.push(node);
        }
    });

    console.log(`Found ${importStatements.length} import statements.`);

    // Sort import statements by source value
    importStatements.sort((a, b) => {
        const sourceA = a.source.value;
        const sourceB = b.source.value;
        return sourceA.localeCompare(sourceB);
    });

    // Group imports by prefix '@word/'
    const groupedImports = [];
    let previousPrefix = null;
    importStatements.forEach(statement => {
        const source = statement.source.value;
        const prefixMatch = source.match(/^@([^/]+)\//);
        const currentPrefix = prefixMatch ? prefixMatch[0] : null;

        if (previousPrefix && previousPrefix !== currentPrefix) {
            groupedImports.push(b.emptyStatement());
        }
        groupedImports.push(statement);
        previousPrefix = currentPrefix;
    });

    // Combine sorted imports and other statements
    ast.program.body = [...groupedImports, ...otherStatements];

    const newContent = recast.print(ast).code;
    if (newContent !== content) {
        fs.writeFileSync(filePath, newContent, "utf-8");
        console.log(`File ${filePath} has been updated.`);
    } else {
        console.log(`No changes made to ${filePath}.`);
    }
}

// Process all .js files in the ../odoo/addons/mail directory
async function processAllFiles() {
    try {
        const jsFiles = await glob("../odoo/addons/mail/**/*.js", { ignore: "node_modules/**" });
        console.log(`Found ${jsFiles.length} files.`);
        for (const filePath of jsFiles) {
            await processFile(filePath);
        }
    } catch (err) {
        console.error("Error finding .js files:", err);
    }
}

processAllFiles();