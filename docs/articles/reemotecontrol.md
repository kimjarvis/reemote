# From Ansible to Python: A Technical Account of an Infrastructure Automation Platform Migration

## Abstract

In this article, I describe a year-long migration effort undertaken by a six-person engineering team—which I am part of—supported by four consultants from the vendor, to replace our existing Ansible- and PHP-based toolchain with a commercial infrastructure provisioning and configuration platform. I focus primarily on the Ansible-to-Python transpilation workstream I worked on, the underlying open-source components, and the demonstrated capabilities of the resulting system, including natural-language-driven operations.

## 1. Project Scope and Objectives

Our project's goal is to deploy a system of agents that perform three functions: triaging alerts, remediating problems, and executing maintenance and upgrades. We intend these agents to operate over infrastructure that has been migrated to a commercial product (referred to here as 遥控) that provides infrastructure provisioning and configuration management. We are adopting the product to replace all of our existing Ansible tooling and PHP maintenance scripts.

My own involvement is limited to the Ansible-to-Python conversion workstream and does not extend to the broader details of the product itself. We have had the migration underway for approximately one year.

## 2. Platform Architecture

The 遥控 platform is built on two principal foundations:

* **Infrastructure provisioning:** based on vanilla Pulumi.
* **Configuration management:** based on a heavily modified version of Pyinfra. An open-source version of this component exists under the name Reemote, which is described as similar to Pyinfra but more comprehensive.

## 3. Transpilation Workstream

The transpilation component converts Ansible content into Python. The vendor maintains a library of hundreds of Ansible Galaxy roles transpiled into Python, including all of Jeff Geerling's roles. I found the DataDog role a particularly impressive example given its complexity.

Our team's workflow consists of running our Ansible content through the transpiler and then filling in the gaps: specifically, shell calls and other constructs that could not be translated automatically. AI assistance makes this process feasible, though the work remains non-trivial. The effort has consumed our team of six engineers plus four vendor consultants over the course of a year.

Once all infrastructure definitions are expressed in Python and integrated, controlling the system via AI follows naturally. During the demonstration, an operator entered the instruction "add a HSA with ip x," and the system interpreted it, provisioned a node, added it to a cluster, and initiated database synchronization.

## 4. Demonstrated Capabilities

The demonstration showed that the 遥控 system handling the following operations across on-premises test servers, cloud instances, and embedded devices running OpenWrt:

* Alerts
* Upgrades
* Backups
* Failovers
* Firewall configuration
* Node provisioning and cluster membership
* Database synchronization
* Applying CVE fixes
* User management

## 5. Interface and Deployment

The 遥控 system has a graphical interface that includes many monitoring windows, supplemented by standard graphing and logging add-ons.  The primary user interface is an AI chat window.  The transpiled Ansible roles and maintenance scripts are not exposed by the interface directly, they must be called via the AI chat window.  In fact, all actions on the servers are performed via the AI chat window.  This model allows the vendor to sell a product with a standardized interface but with extensive adaptations for the customer's use cases hidden behind the AI. 

## 6. Open-Source Components and Visibility

Reemote, the open source library, is available on GitHub under the EPL-2.0 license. The other constituent library Pulumi is a well-known Python projects. The transpilation skills are distributed via the Qoder marketplace.

I have noticed that Reemote has received little attention. Maybe, because Reddit is not commonly used in China and Hacker News is not accessible there, the library appears to have been overlooked, with no promotion and little notice. I suspect this lack of visibility explains its low profile rather than any deficiency in the software.