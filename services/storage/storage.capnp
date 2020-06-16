@0xc4d8bd64d372ceae;

interface Storage {
    set @0 (key: Text, value: Data);
    update @1 (key: Text, value: Data);
    exists @2 (key: Text) -> (exists: Bool);
    get @3 (key: Text) -> (value: Data);
    delete @4 (key: Text);
}

