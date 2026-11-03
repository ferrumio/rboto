"""Compile generic ownership conversion independently of any service protocol."""

import subprocess
from dataclasses import replace
from pathlib import Path

from rboto_codegen.generator import ServiceGenerator
from rboto_codegen.registry import get_service
from rboto_codegen.rust_crate import RustField, RustType

ROOT = Path(__file__).parents[2]


class StorageRenderer(ServiceGenerator):
    def render(self, types: tuple[RustType, ...]) -> str:
        self.crate.types.update({item.name: item for item in types})
        return "\n\n".join(
            self._shared_storage(
                f"Shared{item.name}", f"aws_sdk_sqs::types::{item.name}", item.fields,
            )
            for item in types if self._shared_fields(item.fields)
        )


def field(name: str, rust_type: str, *, optional: bool = False) -> RustField:
    return RustField(name, name, rust_type, optional)


def structure(name: str, *fields: RustField) -> RustType:
    return RustType(name, fields, (), False, False, True)


def test_generated_ownership_moves_payloads_and_releases_siblings(tmp_path: Path) -> None:
    # The descriptor name is deliberately unrelated to any registered service.
    generator = StorageRenderer(
        replace(get_service("sqs"), service_id="future-service"),
        ROOT / "codegen/models/sqs.json", ROOT,
    )
    leaf = structure("TestLeaf", field("body", "::std::string::String"))
    branch = structure(
        "TestBranch",
        field("children", "::std::vec::Vec<::std::option::Option<"
              "::std::boxed::Box<crate::types::TestLeaf>>>"),
        field("named", "::std::collections::HashMap<"
              "::std::string::String, crate::types::TestLeaf>"),
        field("empty", "crate::types::TestLeaf", optional=True),
        field("scalar", "::std::boxed::Box<i32>"),
    )
    node = structure(
        "TestNode",
        field("body", "::std::string::String"),
        field("child", "::std::boxed::Box<crate::types::TestNode>", optional=True),
    )
    root = structure(
        "TestRoot", field("branches", "::std::vec::Vec<crate::types::TestBranch>"),
        field("raw", "::std::collections::HashMap< ::std::string::String, "
              "::std::vec::Vec<::std::string::String>, >"),
    )
    generated = generator.render((leaf, branch, node, root))
    source = r"""
#![allow(dead_code)]
use std::collections::HashMap;
use std::sync::Arc;
mod aws_sdk_sqs {
    pub mod types {
        pub struct TestLeaf { pub body: String }
        pub struct TestBranch {
            pub children: Vec<Option<Box<TestLeaf>>>,
            pub named: std::collections::HashMap<String, TestLeaf>,
            pub empty: Option<TestLeaf>,
            pub scalar: Box<i32>,
        }
        pub struct TestNode {
            pub body: String,
            pub child: Option<Box<TestNode>>,
        }
        pub struct TestRoot {
            pub branches: Vec<TestBranch>,
            pub raw: std::collections::HashMap<String, Vec<String>>,
        }
    }
}
""" + generated + r"""
fn main() {
    use aws_sdk_sqs::types::*;
    let body = "a".repeat(1024 * 1024);
    let address = body.as_ptr() as usize;
    let raw = HashMap::from([("keep".into(), vec!["untouched".into()])]);
    let map_address = &raw["keep"] as *const Vec<String> as usize;
    let original = TestRoot { branches: vec![TestBranch {
        children: vec![Some(Box::new(TestLeaf { body })), None],
        named: HashMap::from([("sibling".into(), TestLeaf { body: "b".repeat(1024 * 1024) })]),
        empty: None,
        scalar: Box::new(42),
    }], raw };
    let output = SharedTestRoot::from(original);
    assert_eq!(&output.raw["keep"] as *const Vec<String> as usize, map_address);
    let branch = Arc::clone(&output.branches[0]);
    let leaf = Arc::clone(branch.children[0].as_ref().unwrap());
    let sibling = Arc::downgrade(branch.named.get("sibling").unwrap());
    assert!(branch.empty.is_none());
    assert!(branch.children[1].is_none());
    assert_eq!(*branch.scalar, 42); // Boxes without native nodes retain their representation.
    assert_eq!(leaf.body.as_ptr() as usize, address); // Moved, never cloned.
    drop(branch);
    drop(output);
    assert!(sibling.upgrade().is_none()); // Keeping a child cannot retain siblings.
    assert_eq!(Arc::strong_count(&leaf), 1);
    assert_eq!(leaf.body.len(), 1024 * 1024);
    std::thread::spawn(move || assert_eq!(leaf.body.as_ptr() as usize, address)).join().unwrap();
    let body = "recursive".repeat(1024);
    let address = body.as_ptr() as usize;
    let recursive = SharedTestNode::from(TestNode {
        body: "parent".into(),
        child: Some(Box::new(TestNode { body, child: None })),
    });
    let child = Arc::clone(recursive.child.as_ref().unwrap());
    drop(recursive);
    assert!(child.child.is_none());
    assert_eq!(child.body.as_ptr() as usize, address);
    assert_eq!(Arc::strong_count(&child), 1);
}
"""
    path = tmp_path / "storage.rs"
    path.write_text(source)
    executable = tmp_path / "storage"
    build = subprocess.run(
        [
            "clippy-driver", "--edition=2024", "-Dwarnings",
            "-Dclippy::redundant_allocation", str(path), "-o", str(executable),
        ],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    subprocess.run([str(executable)], check=True)
