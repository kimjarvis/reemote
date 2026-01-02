## Concurrency example

This example demonstrates concurrent execution.

The class First echos "Hello" and then "World" on both hosts.  These commands are 
executed concurrently.  Only when all the hosts have completed these commands 
and returned responses the class Second echos "Bye" on both hosts.  
